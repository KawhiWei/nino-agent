using Nino.Data.Mcp.Data;
using Npgsql;
using Xunit;

namespace Nino.Data.Mcp.Tests;

public sealed class DataQueryServiceTests
{
    private const string DefaultConnectionString =
        "Host=localhost;Port=55432;Database=nino_data_demo;Username=nino_data_readonly;Password=nino_data_readonly_password";

    [Theory]
    [InlineData("DEMO-202607-001", 60)]
    [InlineData("DEMO-202607-031", 100)]
    [InlineData("DEMO-202607-032", -450)]
    public async Task GetOrderDetail_ComputesExpectedMargin(string orderId, decimal expectedMargin)
    {
        await using var dataSource = CreateDataSource();
        var service = new DataQueryService(dataSource);

        var result = await service.GetOrderDetailAsync(orderId, CancellationToken.None);

        Assert.True(result.Found);
        Assert.Equal(orderId, result.Order?.OrderSerialId);
        Assert.Equal(expectedMargin, result.Totals?.DemoGrossMargin);
    }

    [Fact]
    public async Task QuerySummary_ReturnsAllEffectiveOrdersWithoutChangingTotals()
    {
        await using var dataSource = CreateDataSource();
        var service = new DataQueryService(dataSource);

        var result = await service.QuerySummaryAsync(
            new DateOnly(2026, 7, 1), new DateOnly(2026, 8, 1), "channel",
            CancellationToken.None);

        Assert.Equal(4, result.Groups.Count);
        Assert.Equal(2042, result.Totals.OrderCount);
        Assert.Equal(2282430, result.Totals.CustomerSaleAmount);
        Assert.Equal(1958219, result.Totals.NetSupplierCost);
        Assert.Equal(2282430, result.Totals.PaidAmount);
        Assert.Equal(15770, result.Totals.SuccessfulRefundAmount);
        Assert.Equal(308441, result.Totals.DemoGrossMargin);
        Assert.Equal(2042, result.Groups.Sum(group => group.OrderCount));
        Assert.Equal(2282430, result.Groups.Sum(group => group.CustomerSaleAmount));
        Assert.Equal(1958219, result.Groups.Sum(group => group.NetSupplierCost));
        Assert.Equal(2282430, result.Groups.Sum(group => group.PaidAmount));
        Assert.Equal(15770, result.Groups.Sum(group => group.SuccessfulRefundAmount));
        Assert.Equal(308441, result.Groups.Sum(group => group.DemoGrossMargin));
    }

    [Fact]
    public async Task FindAnomalies_ReturnsWorstMarginsFirst()
    {
        await using var dataSource = CreateDataSource();
        var service = new DataQueryService(dataSource);

        var result = await service.FindAnomaliesAsync(
            new DateOnly(2026, 7, 1), new DateOnly(2026, 8, 1), "negative_margin", 5,
            CancellationToken.None);

        Assert.Equal(5, result.Items.Count);
        Assert.Equal("DEMO-202607-032", result.Items[0].OrderSerialId);
        Assert.Equal(-450, result.Items[0].DemoGrossMargin);
        Assert.Equal("SYN-018615", result.Items[3].OrderSerialId);
        Assert.Equal(-76.5m, result.Items[3].DemoGrossMargin);
        Assert.All(result.Items, item => Assert.True(item.DemoGrossMargin < 0));
        Assert.Equal(
            result.Items.OrderBy(item => item.DemoGrossMargin).Select(item => item.OrderSerialId),
            result.Items.Select(item => item.OrderSerialId));
    }

    [Fact]
    public async Task InvalidParameters_AreRejectedBeforeQueryExecution()
    {
        await using var dataSource = CreateDataSource();
        var service = new DataQueryService(dataSource);
        var cancellationToken = CancellationToken.None;

        await Assert.ThrowsAsync<ArgumentException>(() => service.QuerySummaryAsync(
            new DateOnly(2026, 7, 1), new DateOnly(2026, 8, 1), "raw_sql", cancellationToken));
        await Assert.ThrowsAsync<ArgumentException>(() => service.QuerySummaryAsync(
            new DateOnly(2026, 8, 1), new DateOnly(2026, 7, 1), "day", cancellationToken));
        await Assert.ThrowsAsync<ArgumentOutOfRangeException>(() => service.FindAnomaliesAsync(
            new DateOnly(2026, 7, 1), new DateOnly(2026, 8, 1), "negative_margin", 100,
            cancellationToken));
    }

    private static NpgsqlDataSource CreateDataSource() => NpgsqlDataSource.Create(
        Environment.GetEnvironmentVariable("NINO_DATA_TEST_DB_CONNECTION_STRING")
        ?? DefaultConnectionString);
}
