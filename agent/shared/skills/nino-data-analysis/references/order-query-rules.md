# Order Query Rules（订单查询规则）

- Use the exact order serial ID supplied by the user; never infer a different order.
  中文：必须使用用户提供的准确订单流水号，不得推断其他订单。
- Present customer sales, net supplier cost, paid amount, successful refund amount, and demo gross margin separately.
  中文：分别展示客户销售收入、净供应商成本、已支付金额、成功退款金额和演示毛利。
- Explain negative supplier resources as supplier recovery, not negative purchases.
  中文：负供应商资源应解释为供应商侧回收，不是负采购。
- If the order is absent, state that no matching demo order was found.
  中文：订单不存在时，明确说明未找到匹配的演示订单。
- Every factual claim must be traceable to the order-detail tool result.
  中文：每个事实结论都必须能追溯到订单详情 Tool 结果。
