using System.ComponentModel;
using ModelContextProtocol.Server;
using Nino.Data.Mcp.Data;
using Nino.Data.Mcp.Models;

namespace Nino.Data.Mcp.Tools;

[McpServerToolType]
public sealed class DataTools(IDataQueryService queries)
{
    private const string MetricVersion = "nino-data-2026.07-v1";

    [McpServerTool(Name = "nino_data_get_order_detail", ReadOnly = true, Idempotent = true)]
    [Description("Gets one demo order with customer resources, supplier resources, payments, refunds, and deterministic totals. Use when an exact order_serial_id is known.")]
    public async Task<ToolEnvelope<OrderDetailResult>> GetOrderDetailAsync(
        [Description("Exact demo order serial id, for example DEMO-202607-001.")]
        string orderSerialId,
        CancellationToken cancellationToken)
    {
        var result = await queries.GetOrderDetailAsync(orderSerialId, cancellationToken);
        var warnings = result.Found
            ? Array.Empty<string>()
            : ["No order was found for the supplied order_serial_id."];
        return Envelope(result, warnings);
    }

    [McpServerTool(Name = "nino_data_query_summary", ReadOnly = true, Idempotent = true)]
    [Description("Summarizes paid, non-test demo orders for a half-open date range. Groups only by main_product_type, channel, or day. Amounts are CNY in the demo dataset.")]
    public async Task<ToolEnvelope<DataSummaryResult>> QuerySummaryAsync(
        [Description("Inclusive start date in YYYY-MM-DD format.")] DateOnly startDate,
        [Description("Exclusive end date in YYYY-MM-DD format.")] DateOnly endDate,
        [Description("One of main_product_type, channel, or day.")] string groupBy,
        CancellationToken cancellationToken)
    {
        var result = await queries.QuerySummaryAsync(
            startDate, endDate, groupBy, cancellationToken);
        return Envelope(result,
            ["Demo gross margin excludes tax, commissions, payment fees, and FX gains/losses."]);
    }

    [McpServerTool(Name = "nino_data_find_anomalies", ReadOnly = true, Idempotent = true)]
    [Description("Finds the lowest negative-margin paid, non-test demo orders for a half-open date range and returns deterministic reason codes.")]
    public async Task<ToolEnvelope<DataAnomalyResult>> FindAnomaliesAsync(
        [Description("Inclusive start date in YYYY-MM-DD format.")] DateOnly startDate,
        [Description("Exclusive end date in YYYY-MM-DD format.")] DateOnly endDate,
        [Description("Currently only negative_margin is supported.")] string anomalyType = "negative_margin",
        [Description("Maximum rows from 1 to 20.")] int limit = 5,
        CancellationToken cancellationToken = default)
    {
        var result = await queries.FindAnomaliesAsync(
            startDate, endDate, anomalyType, limit, cancellationToken);
        return Envelope(result,
            ["Reason codes are deterministic MVP rules, not a production reconciliation decision."]);
    }

    private static ToolEnvelope<T> Envelope<T>(T data, IReadOnlyList<string> warnings) =>
        new(Guid.NewGuid().ToString("N"), DateTimeOffset.UtcNow, MetricVersion, data, warnings);
}
