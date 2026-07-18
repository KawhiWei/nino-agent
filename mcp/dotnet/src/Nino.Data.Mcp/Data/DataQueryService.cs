using System.Text.RegularExpressions;
using Nino.Data.Mcp.Models;
using Npgsql;

namespace Nino.Data.Mcp.Data;

public sealed partial class DataQueryService(NpgsqlDataSource dataSource) : IDataQueryService
{
    private static readonly IReadOnlyDictionary<string, string> GroupExpressions =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["main_product_type"] = "COALESCE(o.main_product_type, 'UNKNOWN')",
            ["channel"] = "COALESCE(o.channel, 'UNKNOWN')",
            ["day"] = "to_char(o.order_create_time, 'YYYY-MM-DD')"
        };

    public async Task<OrderDetailResult> GetOrderDetailAsync(
        string orderSerialId,
        CancellationToken cancellationToken)
    {
        orderSerialId = ValidateOrderSerialId(orderSerialId);
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);

        var order = await ReadOrderAsync(connection, orderSerialId, cancellationToken);
        if (order is null)
        {
            return new OrderDetailResult(false, null, [], [], [], [], null);
        }

        var customerResources = await ReadCustomerResourcesAsync(
            connection, orderSerialId, cancellationToken);
        var supplierResources = await ReadSupplierResourcesAsync(
            connection, orderSerialId, cancellationToken);
        var payments = await ReadPaymentsAsync(connection, orderSerialId, cancellationToken);
        var refunds = await ReadRefundsAsync(connection, orderSerialId, cancellationToken);

        var customerSaleAmount = customerResources.Sum(item => item.SaleAmount);
        var netSupplierCost = supplierResources.Sum(item => item.ContractAmount);
        var paidAmount = payments.Sum(item => item.Amount);
        var successfulRefundAmount = refunds
            .Where(item => item.RefundStatus == 2)
            .Sum(item => item.RefundAmount);
        var totals = new OrderDataTotals(
            customerSaleAmount,
            netSupplierCost,
            paidAmount,
            successfulRefundAmount,
            customerSaleAmount - netSupplierCost - successfulRefundAmount);

        return new OrderDetailResult(
            true, order, customerResources, supplierResources, payments, refunds, totals);
    }

    public async Task<DataSummaryResult> QuerySummaryAsync(
        DateOnly startDate,
        DateOnly endDate,
        string groupBy,
        CancellationToken cancellationToken)
    {
        ValidateDateRange(startDate, endDate);
        if (!GroupExpressions.TryGetValue(groupBy, out var groupExpression))
        {
            throw new ArgumentException(
                "group_by must be one of: main_product_type, channel, day.", nameof(groupBy));
        }

        var sql = $$"""
            WITH effective_orders AS (
                SELECT o.*
                FROM nino_data.order_info o
                WHERE o.order_create_time >= @start_date
                  AND o.order_create_time < @end_date
                  AND o.is_test = 0
                  AND EXISTS (
                      SELECT 1 FROM nino_data.pay_info p
                      WHERE p.order_serial_id = o.order_serial_id
                  )
            ), customer_totals AS (
                SELECT order_serial_id, SUM(sale_amount) AS customer_sale_amount
                FROM nino_data.customer_resource_info
                GROUP BY order_serial_id
            ), supplier_totals AS (
                SELECT order_serial_id, SUM(contract_amount) AS net_supplier_cost
                FROM nino_data.supplier_resource_info
                GROUP BY order_serial_id
            ), pay_totals AS (
                SELECT order_serial_id, SUM(amount) AS paid_amount
                FROM nino_data.pay_info
                GROUP BY order_serial_id
            ), refund_totals AS (
                SELECT order_serial_id, SUM(refund_amount) AS successful_refund_amount
                FROM nino_data.refund_info
                WHERE refund_status = 2
                GROUP BY order_serial_id
            )
            SELECT
                {{groupExpression}} AS group_key,
                COUNT(*) AS order_count,
                SUM(COALESCE(c.customer_sale_amount, 0)) AS customer_sale_amount,
                SUM(COALESCE(s.net_supplier_cost, 0)) AS net_supplier_cost,
                SUM(COALESCE(p.paid_amount, 0)) AS paid_amount,
                SUM(COALESCE(r.successful_refund_amount, 0)) AS successful_refund_amount,
                SUM(COALESCE(c.customer_sale_amount, 0)
                    - COALESCE(s.net_supplier_cost, 0)
                    - COALESCE(r.successful_refund_amount, 0)) AS demo_gross_margin,
                COUNT(*) FILTER (
                    WHERE COALESCE(c.customer_sale_amount, 0)
                        - COALESCE(s.net_supplier_cost, 0)
                        - COALESCE(r.successful_refund_amount, 0) < 0
                ) AS negative_margin_order_count
            FROM effective_orders o
            LEFT JOIN customer_totals c USING (order_serial_id)
            LEFT JOIN supplier_totals s USING (order_serial_id)
            LEFT JOIN pay_totals p USING (order_serial_id)
            LEFT JOIN refund_totals r USING (order_serial_id)
            GROUP BY group_key
            ORDER BY group_key;
            """;

        await using var command = dataSource.CreateCommand(sql);
        AddDateRange(command, startDate, endDate);
        var groups = new List<DataSummaryGroup>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            groups.Add(new DataSummaryGroup(
                reader.GetString(0),
                reader.GetInt64(1),
                reader.GetDecimal(2),
                reader.GetDecimal(3),
                reader.GetDecimal(4),
                reader.GetDecimal(5),
                reader.GetDecimal(6),
                reader.GetInt64(7)));
        }

        var totals = new DataSummaryTotals(
            groups.Sum(group => group.OrderCount),
            groups.Sum(group => group.CustomerSaleAmount),
            groups.Sum(group => group.NetSupplierCost),
            groups.Sum(group => group.PaidAmount),
            groups.Sum(group => group.SuccessfulRefundAmount),
            groups.Sum(group => group.DemoGrossMargin),
            groups.Sum(group => group.NegativeMarginOrderCount));

        return new DataSummaryResult(startDate, endDate, groupBy, "CNY", totals, groups);
    }

    public async Task<DataAnomalyResult> FindAnomaliesAsync(
        DateOnly startDate,
        DateOnly endDate,
        string anomalyType,
        int limit,
        CancellationToken cancellationToken)
    {
        ValidateDateRange(startDate, endDate);
        if (!string.Equals(anomalyType, "negative_margin", StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException(
                "anomaly_type currently supports only negative_margin.", nameof(anomalyType));
        }

        if (limit is < 1 or > 20)
        {
            throw new ArgumentOutOfRangeException(nameof(limit), "limit must be between 1 and 20.");
        }

        const string sql = """
            WITH customer_totals AS (
                SELECT order_serial_id, SUM(sale_amount) AS customer_sale_amount
                FROM nino_data.customer_resource_info
                GROUP BY order_serial_id
            ), supplier_totals AS (
                SELECT order_serial_id, SUM(contract_amount) AS net_supplier_cost
                FROM nino_data.supplier_resource_info
                GROUP BY order_serial_id
            ), pay_totals AS (
                SELECT order_serial_id, SUM(amount) AS paid_amount
                FROM nino_data.pay_info
                GROUP BY order_serial_id
            ), refund_totals AS (
                SELECT order_serial_id, SUM(refund_amount) AS successful_refund_amount
                FROM nino_data.refund_info
                WHERE refund_status = 2
                GROUP BY order_serial_id
            ), order_data AS (
                SELECT
                    o.order_serial_id,
                    COALESCE(o.main_product_type, 'UNKNOWN') AS main_product_type,
                    COALESCE(o.channel, 'UNKNOWN') AS channel,
                    o.order_create_time,
                    COALESCE(c.customer_sale_amount, 0) AS customer_sale_amount,
                    COALESCE(s.net_supplier_cost, 0) AS net_supplier_cost,
                    COALESCE(p.paid_amount, 0) AS paid_amount,
                    COALESCE(r.successful_refund_amount, 0) AS successful_refund_amount,
                    COALESCE(c.customer_sale_amount, 0)
                        - COALESCE(s.net_supplier_cost, 0)
                        - COALESCE(r.successful_refund_amount, 0) AS demo_gross_margin
                FROM nino_data.order_info o
                LEFT JOIN customer_totals c USING (order_serial_id)
                LEFT JOIN supplier_totals s USING (order_serial_id)
                LEFT JOIN pay_totals p USING (order_serial_id)
                LEFT JOIN refund_totals r USING (order_serial_id)
                WHERE o.order_create_time >= @start_date
                  AND o.order_create_time < @end_date
                  AND o.is_test = 0
                  AND p.order_serial_id IS NOT NULL
            )
            SELECT *
            FROM order_data
            WHERE demo_gross_margin < 0
            ORDER BY demo_gross_margin, order_serial_id
            LIMIT @limit;
            """;

        await using var command = dataSource.CreateCommand(sql);
        AddDateRange(command, startDate, endDate);
        command.Parameters.AddWithValue("limit", limit);
        var items = new List<DataAnomaly>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            var customerSale = reader.GetDecimal(4);
            var supplierCost = reader.GetDecimal(5);
            var paid = reader.GetDecimal(6);
            var refund = reader.GetDecimal(7);
            var margin = reader.GetDecimal(8);
            var reasons = new List<string> { "NEGATIVE_MARGIN" };
            if (supplierCost > customerSale)
            {
                reasons.Add("SUPPLIER_COST_EXCEEDS_SALES");
            }
            if (refund > 0 && supplierCost >= customerSale - refund)
            {
                reasons.Add("REFUND_NOT_RECOVERED_FROM_SUPPLIER");
            }
            if (paid != customerSale)
            {
                reasons.Add("PAYMENT_SALES_MISMATCH");
            }

            items.Add(new DataAnomaly(
                reader.GetString(0),
                reader.GetString(1),
                reader.GetString(2),
                reader.GetDateTime(3),
                customerSale,
                supplierCost,
                paid,
                refund,
                margin,
                reasons));
        }

        return new DataAnomalyResult(
            startDate, endDate, anomalyType.ToLowerInvariant(), "CNY", items);
    }

    private static async Task<OrderHeader?> ReadOrderAsync(
        NpgsqlConnection connection,
        string orderSerialId,
        CancellationToken cancellationToken)
    {
        const string sql = """
            SELECT order_serial_id, COALESCE(receipt_no, ''), COALESCE(data_serial_id, ''),
                   COALESCE(channel, ''), order_type, project_source, order_scene,
                   order_create_time, sale_amount, sale_currency_amount,
                   COALESCE(currency_type, ''), exchange_rate, is_test,
                   COALESCE(main_product_type, ''), COALESCE(order_source, '')
            FROM nino_data.order_info
            WHERE order_serial_id = @order_serial_id
            LIMIT 1;
            """;
        await using var command = new NpgsqlCommand(sql, connection);
        command.Parameters.AddWithValue("order_serial_id", orderSerialId);
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        if (!await reader.ReadAsync(cancellationToken))
        {
            return null;
        }
        return new OrderHeader(
            reader.GetString(0), reader.GetString(1), reader.GetString(2), reader.GetString(3),
            reader.GetInt32(4), reader.GetInt32(5), reader.GetInt32(6), reader.GetDateTime(7),
            reader.GetDecimal(8), reader.GetDecimal(9), reader.GetString(10), reader.GetDecimal(11),
            reader.GetInt32(12), reader.GetString(13), reader.GetString(14));
    }

    private static async Task<IReadOnlyList<CustomerResource>> ReadCustomerResourcesAsync(
        NpgsqlConnection connection,
        string orderSerialId,
        CancellationToken cancellationToken)
    {
        const string sql = """
            SELECT row_guid, resource_type, COALESCE(gold_toad_resource_type, ''),
                   COALESCE(business_guid, ''), resource_count, sale_amount,
                   sale_currency_amount, COALESCE(currency, ''), exchange_rate, resource_state
            FROM nino_data.customer_resource_info
            WHERE order_serial_id = @order_serial_id
            ORDER BY row_guid
            LIMIT 200;
            """;
        await using var command = new NpgsqlCommand(sql, connection);
        command.Parameters.AddWithValue("order_serial_id", orderSerialId);
        var items = new List<CustomerResource>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            items.Add(new CustomerResource(
                reader.GetString(0), reader.GetInt32(1), reader.GetString(2), reader.GetString(3),
                reader.GetInt32(4), reader.GetDecimal(5), reader.GetDecimal(6), reader.GetString(7),
                reader.GetDecimal(8), reader.GetInt32(9)));
        }
        return items;
    }

    private static async Task<IReadOnlyList<SupplierResource>> ReadSupplierResourcesAsync(
        NpgsqlConnection connection,
        string orderSerialId,
        CancellationToken cancellationToken)
    {
        const string sql = """
            SELECT row_guid, resource_type, COALESCE(gold_toad_resource_type, ''),
                   COALESCE(business_guid, ''), resource_count, contract_amount,
                   contract_currency_amount, COALESCE(currency, ''), exchange_rate,
                   COALESCE(merchant_id, ''), COALESCE(electronic_ticket_no, ''),
                   COALESCE(supplier_trade_no, ''), resource_state
            FROM nino_data.supplier_resource_info
            WHERE order_serial_id = @order_serial_id
            ORDER BY row_guid
            LIMIT 200;
            """;
        await using var command = new NpgsqlCommand(sql, connection);
        command.Parameters.AddWithValue("order_serial_id", orderSerialId);
        var items = new List<SupplierResource>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            items.Add(new SupplierResource(
                reader.GetString(0), reader.GetInt32(1), reader.GetString(2), reader.GetString(3),
                reader.GetInt32(4), reader.GetDecimal(5), reader.GetDecimal(6), reader.GetString(7),
                reader.GetDecimal(8), reader.GetString(9), reader.GetString(10), reader.GetString(11),
                reader.GetInt32(12)));
        }
        return items;
    }

    private static async Task<IReadOnlyList<PaymentInfo>> ReadPaymentsAsync(
        NpgsqlConnection connection,
        string orderSerialId,
        CancellationToken cancellationToken)
    {
        const string sql = """
            SELECT row_guid, pay_type, COALESCE(pay_type_description, ''), COALESCE(trade_no, ''),
                   amount, COALESCE(currency, ''), exchange_rate, COALESCE(pay_channel_code, ''),
                   COALESCE(pay_product_code, ''), "UpdateTime"
            FROM nino_data.pay_info
            WHERE order_serial_id = @order_serial_id
            ORDER BY "CreateTime"
            LIMIT 100;
            """;
        await using var command = new NpgsqlCommand(sql, connection);
        command.Parameters.AddWithValue("order_serial_id", orderSerialId);
        var items = new List<PaymentInfo>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            items.Add(new PaymentInfo(
                reader.GetString(0), reader.GetInt32(1), reader.GetString(2), reader.GetString(3),
                reader.GetDecimal(4), reader.GetString(5), reader.GetDecimal(6), reader.GetString(7),
                reader.GetString(8), reader.GetDateTime(9)));
        }
        return items;
    }

    private static async Task<IReadOnlyList<RefundInfo>> ReadRefundsAsync(
        NpgsqlConnection connection,
        string orderSerialId,
        CancellationToken cancellationToken)
    {
        const string sql = """
            SELECT row_guid, COALESCE(refund_no, ''), refund_amount, refund_status,
                   COALESCE(trade_no, ''), COALESCE(refund_trade_no, ''),
                   COALESCE(refund_reason, ''), COALESCE(refund_finish_time, ''),
                   COALESCE(business_apply_refund_currency_type, '')
            FROM nino_data.refund_info
            WHERE order_serial_id = @order_serial_id
            ORDER BY "CreateTime"
            LIMIT 100;
            """;
        await using var command = new NpgsqlCommand(sql, connection);
        command.Parameters.AddWithValue("order_serial_id", orderSerialId);
        var items = new List<RefundInfo>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            items.Add(new RefundInfo(
                reader.GetString(0), reader.GetString(1), reader.GetDecimal(2), reader.GetInt32(3),
                reader.GetString(4), reader.GetString(5), reader.GetString(6), reader.GetString(7),
                reader.GetString(8)));
        }
        return items;
    }

    private static string ValidateOrderSerialId(string orderSerialId)
    {
        var normalized = orderSerialId?.Trim() ?? string.Empty;
        if (normalized.Length is < 1 or > 64 || !OrderSerialIdPattern().IsMatch(normalized))
        {
            throw new ArgumentException(
                "order_serial_id must be 1-64 letters, numbers, dot, underscore, or dash.",
                nameof(orderSerialId));
        }
        return normalized;
    }

    private static void ValidateDateRange(DateOnly startDate, DateOnly endDate)
    {
        var days = endDate.DayNumber - startDate.DayNumber;
        if (days is < 1 or > 366)
        {
            throw new ArgumentException(
                "Date range must be a half-open interval between 1 and 366 days.");
        }
    }

    private static void AddDateRange(NpgsqlCommand command, DateOnly startDate, DateOnly endDate)
    {
        command.Parameters.AddWithValue("start_date", startDate.ToDateTime(TimeOnly.MinValue));
        command.Parameters.AddWithValue("end_date", endDate.ToDateTime(TimeOnly.MinValue));
    }

    [GeneratedRegex("^[A-Za-z0-9._-]+$", RegexOptions.CultureInvariant)]
    private static partial Regex OrderSerialIdPattern();
}
