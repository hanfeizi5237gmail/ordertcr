# 工作簿视觉与复读检查

2026-11-07 使用 bundled `soffice --headless --convert-to pdf` 将松北站最新测试订单渲染为 PDF，再用 `pdftoppm` 查看第一页 [order_landscape.png](rendered2/order_landscape.png)。已确认横向页面、异常行的浅黄色/浅红色整行填充、正常行与异常行的金额空值/数值分布可见；`openpyxl` 复读另外确认表头、列数、原生批注和颜色值。渲染使用本地字体环境，中文表头在该无界面渲染环境中显示不稳定，因此表头语义以 XLSX `openpyxl` 复读为准，未宣称 Excel 客户端视觉兼容。

复读范围：三份真实聊天生成的 159 行标准订单、合并财务草稿及独立首次运行的星月 42 行；检查 10 列、颜色、原文批注、目录版本批注、空金额和小计标签。没有使用 Excel GUI，也没有执行真实订单系统导入。

最终规则修正后，再以 macOS Quick Look 直接预览最新的三张源 XLSX：[松北](rendered2/songbei/测试_下单需求_待配送_待人工处理.xlsx.png)、[星月](rendered2/xingyue/测试_下单需求_待配送_待人工处理.xlsx.png)、[学院路](rendered2/xueyuan/测试_下单需求_待配送_待人工处理.xlsx.png)。三表中文表头和十列均可见，异常行整行浅黄/浅红、未确认金额留空，尾行是“已确认金额小计”；没有发现文字被截断为 `####`。财务草稿的两张表也从最终文件临时分拆作 Quick Look 预览（原件未改变），但长表缩略图不构成完整分页验收。保存后原生批注仍由 `openpyxl` 复读验证，Quick Look 缩略图本身不显示批注浮窗。
