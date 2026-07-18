namespace Nino.Data.Mcp.Models;

public sealed record ToolEnvelope<T>(
    string QueryId,
    DateTimeOffset SnapshotAt,
    string MetricDefinitionVersion,
    T Data,
    IReadOnlyList<string> Warnings);

public sealed record OrderHeader(
    string OrderSerialId,
    string ReceiptNo,
    string DataSerialId,
    string Channel,
    int OrderType,
    int ProjectSource,
    int OrderScene,
    DateTime OrderCreateTime,
    decimal SaleAmount,
    decimal SaleCurrencyAmount,
    string CurrencyType,
    decimal ExchangeRate,
    int IsTest,
    string MainProductType,
    string OrderSource);

public sealed record CustomerResource(
    string RowGuid,
    int ResourceType,
    string GoldToadResourceType,
    string BusinessGuid,
    int ResourceCount,
    decimal SaleAmount,
    decimal SaleCurrencyAmount,
    string Currency,
    decimal ExchangeRate,
    int ResourceState);

public sealed record SupplierResource(
    string RowGuid,
    int ResourceType,
    string GoldToadResourceType,
    string BusinessGuid,
    int ResourceCount,
    decimal ContractAmount,
    decimal ContractCurrencyAmount,
    string Currency,
    decimal ExchangeRate,
    string MerchantId,
    string ElectronicTicketNo,
    string SupplierTradeNo,
    int ResourceState);

public sealed record PaymentInfo(
    string RowGuid,
    int PayType,
    string PayTypeDescription,
    string TradeNo,
    decimal Amount,
    string Currency,
    decimal ExchangeRate,
    string PayChannelCode,
    string PayProductCode,
    DateTime PaidAt);

public sealed record RefundInfo(
    string RowGuid,
    string RefundNo,
    decimal RefundAmount,
    int RefundStatus,
    string TradeNo,
    string RefundTradeNo,
    string RefundReason,
    string RefundFinishTime,
    string BusinessApplyRefundCurrencyType);

public sealed record OrderDataTotals(
    decimal CustomerSaleAmount,
    decimal NetSupplierCost,
    decimal PaidAmount,
    decimal SuccessfulRefundAmount,
    decimal DemoGrossMargin);

public sealed record OrderDetailResult(
    bool Found,
    OrderHeader? Order,
    IReadOnlyList<CustomerResource> CustomerResources,
    IReadOnlyList<SupplierResource> SupplierResources,
    IReadOnlyList<PaymentInfo> Payments,
    IReadOnlyList<RefundInfo> Refunds,
    OrderDataTotals? Totals);

public sealed record DataSummaryGroup(
    string Group,
    long OrderCount,
    decimal CustomerSaleAmount,
    decimal NetSupplierCost,
    decimal PaidAmount,
    decimal SuccessfulRefundAmount,
    decimal DemoGrossMargin,
    long NegativeMarginOrderCount);

public sealed record DataSummaryTotals(
    long OrderCount,
    decimal CustomerSaleAmount,
    decimal NetSupplierCost,
    decimal PaidAmount,
    decimal SuccessfulRefundAmount,
    decimal DemoGrossMargin,
    long NegativeMarginOrderCount);

public sealed record DataSummaryResult(
    DateOnly StartDate,
    DateOnly EndDate,
    string GroupBy,
    string Currency,
    DataSummaryTotals Totals,
    IReadOnlyList<DataSummaryGroup> Groups);

public sealed record DataAnomaly(
    string OrderSerialId,
    string MainProductType,
    string Channel,
    DateTime OrderCreateTime,
    decimal CustomerSaleAmount,
    decimal NetSupplierCost,
    decimal PaidAmount,
    decimal SuccessfulRefundAmount,
    decimal DemoGrossMargin,
    IReadOnlyList<string> ReasonCodes);

public sealed record DataAnomalyResult(
    DateOnly StartDate,
    DateOnly EndDate,
    string AnomalyType,
    string Currency,
    IReadOnlyList<DataAnomaly> Items);
