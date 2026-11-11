"""Normal business entry: text -> routing -> shared concepts -> audited workbooks.

No evaluation manifests, labels, historical decisions, or caller-parsed items
are accepted at this boundary. Internal line IDs are derived from actual text.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from .matching import _normalized, match_item
from .parsing import parse_chat
from .workflow import process_batch, merge_finance, validate_config, _write_json
from .catalog import CatalogStore
from .knowledge import load_knowledge, match_shared, company_knowledge

ALLOWED = {'schema_version','batch_id','chat_files','config','shipping_date','company',
           'matching_mode','test_only','discount'}
UNASSIGNED = {'company_id':'unassigned','full_name':'归属待核','aliases':[], 'finance_name':'归属待核'}


def _issue(item,code,message):
    if not any(i['code']==code for i in item['issues']):
        item['issues'].append({'code':code,'message':message})


def _date_context(context,shipping):
    if not context:return shipping
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',context):return date.fromisoformat(context).isoformat()
    match=re.fullmatch(r'(?:(\d{4})年)?(\d{1,2})[月.](\d{1,2})(?:日|号)?',context)
    if not match:raise ValueError('日期范围/相对日期缺少明确单日依据')
    if not match[1] and abs(int(match[2])-int(shipping[5:7]))>=6:
        raise ValueError('无年份日期可能跨年，不能套用批次年份')
    return date(int(match[1] or shipping[:4]),int(match[2]),int(match[3])).isoformat()


def _resolve_events(items, identity_keys=None, unsafe_names=None, unsafe_identities=None,
                    control_identities=None):
    """Resolve only explicit, unique predecessors within this conversation."""
    active=defaultdict(list)
    for position, item in enumerate(items):
        catalog_identity = (identity_keys or {}).get(item['line_id'])
        # Catalog identities are control-link witnesses, not general duplicate
        # keys. Ordinary events retain the existing text-key policy unless
        # their identity participates in this batch's control chain.
        if (catalog_identity is not None and
                (item.get('event') in ('改量', '取消') or
                 (item['routed_company'], item['routed_date'], catalog_identity)
                 in (control_identities or set()))):
            identity = catalog_identity
        else:
            identity = ('text', _normalized(item['name']))
        key=(item['routed_company'],item['routed_date'],identity)
        item['event_state']='active'
        previous=active[key]
        text_key = (item['routed_company'], item['routed_date'], _normalized(item['name']))
        if (text_key in (unsafe_names or set()) or
                (catalog_identity is not None and
                 (item['routed_company'], item['routed_date'], catalog_identity)
                 in (unsafe_identities or set()))):
            item['event_state'] = 'pending'
            item['quantity_after_event'] = None
            _issue(item, 'event_pending', '同名需求存在未确认或冲突的商品要求，控制关系须人工核定')
            continue
        def net_quantity(group):
            if (not group or len({row.get('unit') for row in group}) != 1
                    or any(row.get('event_state') == 'pending' or
                           any(i['code'] == 'event_pending' for i in row['issues']) for row in group)
                    or any(row.get('quantity') is None or Decimal(row['quantity']) <= 0 for row in group)):
                return None
            return format(sum((Decimal(row['quantity']) for row in group), Decimal(0)).normalize(), 'f')
        if item.get('event') == '重复':
            if (len(previous) == 1 and position > 0 and items[position - 1] is previous[0]
                    and previous[0].get('quantity') == item.get('quantity')
                    and previous[0].get('unit') == item.get('unit')):
                item['predecessor_line_id'] = previous[0]['line_id']
                item['event_state'] = 'duplicate_event'
                item['event_resolution'] = 'explicit_retransmission'
                item['quantity_after_event'] = net_quantity(previous)
                _issue(item, 'duplicate_record', '原文明示重复转发且数量单位与唯一前项相同；保留记录，不新增采购量')
            else:
                item['event_state'] = 'pending'
                _issue(item, 'event_predecessor_ambiguous', '重复转发与前项不能唯一核对，保留待人工')
            continue
        if item.get('event') in ('改量','取消'):
            total_change = item.get('event') == '改量' and item.get('amendment_scope') == 'total'
            invalid_change = (item.get('event') == '改量' and
                              (item.get('quantity') is None or Decimal(item['quantity']) <= 0
                               or not item.get('unit')))
            partial_cancel = (item.get('event') == '取消' and item.get('quantity') is not None
                              and (len(previous) != 1 or
                                   item['quantity'] != previous[0].get('quantity') or
                                   item.get('unit') != previous[0].get('unit')))
            invalid_cancel = (item.get('event') == '取消' and
                              any(issue['code'] == 'invalid_quantity' for issue in item['issues']))
            if not previous or len(previous)!=1 and not total_change or invalid_change or partial_cancel or invalid_cancel:
                item['event_state']='pending'
                _issue(item,'event_predecessor_ambiguous','改量/取消没有唯一的同公司同日期前项，须人工核定')
                for prior in previous:
                    prior['event_state'] = 'pending'
                    prior['quantity_after_event'] = None
                    _issue(prior,'event_pending','后续改量/取消关系不唯一，本行暂不计入金额')
                continue
            item['predecessor_line_id']=previous[-1]['line_id']
            item['predecessor_line_ids']=[row['line_id'] for row in previous]
            for predecessor in previous:
                predecessor['event_state']='superseded' if item['event']=='改量' else 'inactive'
                predecessor['successor_line_id']=item['line_id']
                _issue(predecessor,'event_superseded','已被明确改量/取消；保留原需求及关系，不计入当前有效金额')
            item['event_resolution']='unique_text_predecessor'
            active[key]=[]
            if item['event']=='取消':
                item['event_state']='cancellation_record'
                item['quantity_after_event']='0'
                item['issues']=[i for i in item['issues'] if i['code'] not in ('missing_quantity','missing_unit')]
                _issue(item,'cancellation_record','取消已关联唯一前项；本行是控制记录，不是新增采购量')
                continue
            previous = active[key]
        if item.get('event')=='追加' and '这样的' in str(item.get('notes') or ''):
            item['event_state']='pending'
            _issue(item,'unresolved_reference','“这样的”缺少可核定的商品规格或前单指代，须人工确认')
        if not item.get('event') and previous:
            item['event_state'] = 'pending'
            _issue(item, 'event_predecessor_ambiguous', '同公司同日同品再次下单，但没有追加、改量或重复转发依据')
            for prior in previous:
                prior['event_state'] = 'pending'
                prior['quantity_after_event'] = None
                _issue(prior, 'event_pending', '同日同品后续需求关系未明；保留原量，暂不合计计价')
        if item.get('event') == '追加' and item['event_state'] == 'active':
            item['event_resolution'] = 'explicit_named_append'
            if previous:
                item['predecessor_line_id'] = previous[-1]['line_id']
        active[key].append(item)
        item['quantity_after_event'] = net_quantity(active[key])


def process_messages(runtime_root,request,output_root,*,base_dir=None):
    if not isinstance(request,dict) or set(request)-ALLOWED:
        raise ValueError('run-messages只接正常原文输入；不接受items、decisions或评测元数据')
    if request.get('schema_version')!=1:raise ValueError('消息请求schema_version无效')
    config=json.loads(json.dumps(request.get('config'),ensure_ascii=False))
    validate_config(config)
    if config.get('aliases') or config.get('unit_rules'):
        raise ValueError('原文入口不得通过配置注入SKU答案；请维护正式共享知识或有据公司约定')
    mode=request.get('matching_mode','shared')
    if mode not in ('catalog_only','shared','shared_with_company_contract'):
        raise ValueError('原文入口不加载历史profile或逐项历史决策')
    shipping=request.get('shipping_date')
    if not isinstance(shipping,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',shipping):
        raise ValueError('需由用户明确完整发货日期YYYY-MM-DD')
    date.fromisoformat(shipping)
    batch_id=request.get('batch_id')
    if not isinstance(batch_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,55}',batch_id):
        raise ValueError('消息batch_id须为1到55位字母数字下划线或连字符')
    lookup={str(name).casefold():c['company_id'] for c in config['companies']
            for name in [c['company_id'],c['full_name'],*c.get('aliases',[])]}
    fallback=None
    if request.get('company'):
        fallback=lookup.get(str(request['company']).casefold())
        if not fallback:raise ValueError('批次所属公司不在初始化范围')
    if any(c['company_id']=='unassigned' for c in config['companies']):
        raise ValueError('unassigned为系统待核归属标识，请使用其他company_id')
    config['companies'].append(dict(UNASSIGNED))
    base=Path(base_dir or '.').resolve()
    files=request.get('chat_files')
    if not isinstance(files,list) or not files:raise ValueError('chat_files不能为空')
    product_names = {record['name'] for record in CatalogStore(runtime_root).current()['records']}
    if mode != 'catalog_only':
        knowledge, _ = load_knowledge()
        product_names.update(alias for rule in knowledge['rules'] for alias in rule['aliases'])
    items=[];sources=[];names=set()
    for value in files:
        if not isinstance(value,str) or not value:raise ValueError('聊天路径须为非空文本')
        path=(base/value).resolve()
        if path.name in names:raise ValueError('聊天文件同名，来源不可区分')
        names.add(path.name);raw=path.read_bytes();text=raw.decode('utf-8-sig')
        sources.append({'source_name':path.name,'path':str(path),'sha256':hashlib.sha256(raw).hexdigest()})
        for item in parse_chat(text,path.name,company_aliases=list(lookup),product_names=product_names):
            company=lookup.get(str(item.get('target_station') or '').casefold())
            item['routed_company']=company or (fallback if not item.get('target_station') else None) or 'unassigned'
            if item['routed_company']=='unassigned':
                _issue(item,'company_boundary','文本和已确认批次上下文未给出唯一所属公司，须人工分单')
            try:item['routed_date']=_date_context(item.get('date_context'),shipping)
            except ValueError:
                item['routed_date']=shipping
                _issue(item,'date_context','原始日期上下文不是已明确的单日，保留原文待核')
            # Canonical date is derived from text plus the user-supplied year.
            item['original_date_context']=item.get('date_context')
            item['date_context']=item['routed_date']
            items.append(item)
    if not items:raise ValueError('没有可处理的商品事件')
    identity_keys = {}
    if any(item.get('event') in ('改量', '取消') for item in items):
        records = CatalogStore(runtime_root).current()['records']
        shared = load_knowledge()[0] if mode != 'catalog_only' else None
        knowledge_by_company = {}
        cache = {}
        for item in items:
            company = item['routed_company']
            key = (company, mode, item.get('name'), item.get('notes'), item.get('unit'),
                   item.get('quantity'), item.get('event'), item.get('raw_text'))
            if key not in cache:
                # Remove only the already-parsed control words from the matcher input;
                # retain all notes, including brand, processing and negative requirements.
                probe = dict(item)
                if item.get('event') in ('改量', '取消'):
                    probe['raw_text'] = ' '.join(filter(None, (item.get('name'), item.get('notes'))))
                if shared is not None:
                    if company not in knowledge_by_company:
                        knowledge_by_company[company] = (company_knowledge(shared, company)[0]
                            if mode == 'shared_with_company_contract' else shared)
                    matched = match_shared(probe, records, knowledge_by_company[company])
                else:
                    matched = match_item(probe, records)
                selected = matched.get('selected')
                quantity_only = {'missing_quantity', 'missing_unit', 'invalid_quantity', 'quantity_conversion'}
                identity_issues = [issue for issue in matched.get('issues', [])
                                   if issue['code'] not in quantity_only]
                cache[key] = (('catalog', str(selected['code']))
                              if selected and matched.get('product_status') == 'matched'
                              and not identity_issues else None)
            identity_keys[item['line_id']] = cache[key]
        by_name = defaultdict(list)
        for item in items:
            text_key = (item['routed_company'], item['routed_date'], _normalized(item['name']))
            by_name[text_key].append(item)
        unsafe_names = set()
        unsafe_identities = set()
        control_identities = set()
        control_lines = set()
        for text_key, group in by_name.items():
            identities = {identity_keys[item['line_id']] for item in group}
            if any(item.get('event') in ('改量', '取消') for item in group) and (
                    None in identities or len(identities) > 1):
                unsafe_names.add(text_key)
                for identity in identities:
                    if identity is not None:
                        unsafe_identities.add((text_key[0], text_key[1], identity))
        positions = {item['line_id']: index for index, item in enumerate(items)}
        for item in items:
            if item.get('event') not in ('改量', '取消'):
                continue
            identity = identity_keys.get(item['line_id'])
            if identity is None:
                continue
            scoped = (item['routed_company'], item['routed_date'], identity)
            control_identities.add(scoped)
            for prior in items[:positions[item['line_id']]]:
                if (prior['routed_company'], prior['routed_date'],
                        identity_keys.get(prior['line_id'])) == scoped:
                    control_lines.add(prior['line_id'])
        # An unresolved same-name constraint makes every equivalent witness
        # in its company/date control group pending, without affecting an
        # unrelated identity elsewhere in the batch.
        control_identities.update(unsafe_identities)
        identity_keys = {line_id: identity for line_id, identity in identity_keys.items()
                         if identity is not None and
                         ((next(item for item in items if item['line_id'] == line_id)['routed_company'],
                           next(item for item in items if item['line_id'] == line_id)['routed_date'],
                           identity) in control_identities or line_id in control_lines)}
    else:
        unsafe_names = set()
        unsafe_identities = set()
        control_identities = set()
    _resolve_events(items, identity_keys, unsafe_names, unsafe_identities, control_identities)
    groups=defaultdict(list)
    for item in items:groups[(item['routed_company'],item['routed_date'])].append(item)
    manifests=[];row_lookup={}
    for index,((company,day),group) in enumerate(groups.items(),1):
        batch={'schema_version':1,'batch_id':f'{batch_id}-g{index}','company':company,'shipping_date':day,
               'config':config,'mode':'order','date_policy':'single','date_policy_confirmed':True,
               'publish':False,'test_only':request.get('test_only',False),'matching_mode':mode,'items':group}
        if 'discount' in request:batch['discount']=request['discount']
        manifest=process_batch(runtime_root,batch,output_root,base_dir=base)
        manifests.append(manifest)
        for excel_row,row in enumerate(manifest['order']['rows'],2):
            row_lookup[row['line_id']]={'row':row,'company_id':company,'shipping_date':day,
                'manifest_path':manifest['manifest_path'],'excel_row':excel_row}
    merged=merge_finance(runtime_root,[m['manifest_path'] for m in manifests],Path(output_root)/'merged')
    catalog=CatalogStore(runtime_root).current()
    result={'schema_version':1,'batch_id':batch_id,'matching_mode':mode,'event_count':len(items),
            'sources':sources,'catalog':{k:v for k,v in catalog.items() if k!='records'},
            'runtime_inputs':'raw_chat+confirmed_company_date+current_catalog+formal_knowledge',
            'historical_decisions_loaded':False,
            'events':[row_lookup[i['line_id']] for i in items],
            'manifests':[m['manifest_path'] for m in manifests],'merged_finance':merged}
    _write_json(Path(output_root)/batch_id/'messages-result.json',result)
    return result
