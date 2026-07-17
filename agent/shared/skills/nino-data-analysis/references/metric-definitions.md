# Metric Definitions

- Currency is CNY for the demo dataset.
- Date ranges are half-open: start date inclusive, end date exclusive.
- Effective aggregate orders are non-test orders with at least one payment record.
- Customer sales equal the sum of customer resource `sale_amount`.
- Net supplier cost equals the sum of supplier resource `contract_amount`; negative values represent supplier recovery.
- Successful refunds include only rows where `refund_status = 2`.
- Demo gross margin equals customer sales minus net supplier cost minus successful refunds.
- The demo margin excludes tax, commission, payment fees, and foreign-exchange gains or losses.

