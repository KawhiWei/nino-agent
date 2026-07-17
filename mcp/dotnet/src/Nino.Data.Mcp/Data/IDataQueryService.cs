using Nino.Data.Mcp.Models;

namespace Nino.Data.Mcp.Data;

public interface IDataQueryService
{
    Task<OrderDetailResult> GetOrderDetailAsync(
        string orderSerialId, CancellationToken cancellationToken);

    Task<DataSummaryResult> QuerySummaryAsync(
        DateOnly startDate,
        DateOnly endDate,
        string groupBy,
        CancellationToken cancellationToken);

    Task<DataAnomalyResult> FindAnomaliesAsync(
        DateOnly startDate,
        DateOnly endDate,
        string anomalyType,
        int limit,
        CancellationToken cancellationToken);
}

