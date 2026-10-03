# auction.com field comparison — auction_20260927.jsonl vs auction_20261003.jsonl
Generated 2026-10-03T09:00:50+00:00Z

| file | rows | trustee-sale rows | distinct fields |
|---|---|---|---|
| auction_20260927.jsonl | 16320 | 11409 | 27 |
| auction_20261003.jsonl | 15926 | 10982 | 85 |

## Fields Ted asked about

| asked | field(s) in new file | status | non-null in new | non-null on trustee sales |
|---|---|---|---|---|
| venue name | `venue_name` | NEW | 11508 / 15926 (72.3%) | 10588 / 10982 (96.4%) |
| venue address | `venue_address` | NEW | 11091 / 15926 (69.6%) | 10588 / 10982 (96.4%) |
| registration time | `registration_time` | ABSENT — API never returns it | 0 / 15926 (0.0%) | 0 / 10982 (0.0%) |
| auction time | `auction_time_local` | NEW | 8939 / 15926 (56.1%) | 8939 / 10982 (81.4%) |
| auction time | `auction_start_utc` | NEW | 15167 / 15926 (95.2%) | 10936 / 10982 (99.6%) |
| opening bid | `opening_bid` | NEW | 6540 / 15926 (41.1%) | 1720 / 10982 (15.7%) |
| opening bid | `nos_amount` | ABSENT — API never returns it | 0 / 15926 (0.0%) | 0 / 10982 (0.0%) |
| opening bid | `minimum_credit_bid_amount` | NEW | 32 / 15926 (0.2%) | 32 / 10982 (0.3%) |
| foreclosing attorney | `foreclosing_attorney` | NEW | 10982 / 15926 (69.0%) | 10982 / 10982 (100.0%) |
| foreclosing attorney | `foreclosing_attorney_phone` | NEW | 9083 / 15926 (57.0%) | 9083 / 10982 (82.7%) |
| foreclosing attorney | `foreclosing_attorney_email` | NEW | 2672 / 15926 (16.8%) | 2672 / 10982 (24.3%) |

## New fields (in new file, not in old)

| field | non-null | % | non-null on trustee sales |
|---|---|---|---|
| `apn` | 9236 | 58.0% | 6985 / 10982 |
| `auction_date` | 15383 | 96.6% | 10978 / 10982 |
| `auction_end_utc` | 4610 | 28.9% | 395 / 10982 |
| `auction_is_online` | 15926 | 100.0% | 10982 / 10982 |
| `auction_start_utc` | 15167 | 95.2% | 10936 / 10982 |
| `auction_time_local` | 8939 | 56.1% | 8939 / 10982 |
| `beneficiary` | 7349 | 46.1% | 7349 / 10982 |
| `broker` | 1168 | 7.3% | 0 / 10982 |
| `broker_phone` | 1530 | 9.6% | 0 / 10982 |
| `collateral_estimate` | 0 | 0.0% | 0 / 10982 |
| `event_id` | 15926 | 100.0% | 10982 / 10982 |
| `event_title` | 15926 | 100.0% | 10982 / 10982 |
| `event_type` | 15926 | 100.0% | 10982 / 10982 |
| `final_judgement_amount` | 0 | 0.0% | 0 / 10982 |
| `foreclosing_attorney` | 10982 | 69.0% | 10982 / 10982 |
| `foreclosing_attorney_email` | 2672 | 16.8% | 2672 / 10982 |
| `foreclosing_attorney_phone` | 9083 | 57.0% | 9083 / 10982 |
| `investor` | 10861 | 68.2% | 10861 / 10982 |
| `latitude` | 15926 | 100.0% | 10982 / 10982 |
| `legal_description` | 8052 | 50.6% | 7574 / 10982 |
| `listing_agent` | 1943 | 12.2% | 0 / 10982 |
| `longitude` | 15926 | 100.0% | 10982 / 10982 |
| `marketing_tags` | 13871 | 87.1% | 10034 / 10982 |
| `minimum_credit_bid_amount` | 32 | 0.2% | 32 / 10982 |
| `nos_amount` | 0 | 0.0% | 0 / 10982 |
| `online_state_deposit_amount` | 250 | 1.6% | 250 / 10982 |
| `opening_bid` | 6540 | 41.1% | 1720 / 10982 |
| `parcel_numbers` | 9172 | 57.6% | 6952 / 10982 |
| `parties` | 15926 | 100.0% | 10982 / 10982 |
| `postponement_new_auction_date` | 9 | 0.1% | 9 / 10982 |
| `postponement_new_auction_time` | 10 | 0.1% | 10 / 10982 |
| `principal_original_amount` | 1580 | 9.9% | 1580 / 10982 |
| `redemption_expiration_date` | 1007 | 6.3% | 0 / 10982 |
| `registration_time` | 0 | 0.0% | 0 / 10982 |
| `sale_instruction` | 0 | 0.0% | 0 / 10982 |
| `seller_current_value` | 9696 | 60.9% | 8514 / 10982 |
| `servicer` | 11725 | 73.6% | 10982 / 10982 |
| `special_notes` | 302 | 1.9% | 302 / 10982 |
| `total_estimated_debt` | 0 | 0.0% | 0 / 10982 |
| `trustee_reference_id` | 5295 | 33.2% | 5295 / 10982 |
| `trustee_sale_number` | 9095 | 57.1% | 9095 / 10982 |
| `trustee_sales_agent` | 0 | 0.0% | 0 / 10982 |
| `trustee_sales_channel` | 15269 | 95.9% | 10982 / 10982 |
| `trustor` | 8654 | 54.3% | 8654 / 10982 |
| `venue_address` | 11091 | 69.6% | 10588 / 10982 |
| `venue_city` | 10782 | 67.7% | 10279 / 10982 |
| `venue_county` | 11091 | 69.6% | 10588 / 10982 |
| `venue_directions` | 3 | 0.0% | 3 / 10982 |
| `venue_id` | 11508 | 72.3% | 10588 / 10982 |
| `venue_lat` | 9072 | 57.0% | 9072 / 10982 |
| `venue_lon` | 9072 | 57.0% | 9072 / 10982 |
| `venue_name` | 11508 | 72.3% | 10588 / 10982 |
| `venue_state` | 11091 | 69.6% | 10588 / 10982 |
| `venue_street` | 10782 | 67.7% | 10279 / 10982 |
| `venue_title` | 11508 | 72.3% | 10588 / 10982 |
| `venue_type` | 11508 | 72.3% | 10588 / 10982 |
| `venue_url` | 6383 | 40.1% | 6383 / 10982 |
| `venue_zip` | 10782 | 67.7% | 10279 / 10982 |

## Dropped fields (in old file, not in new)

none

## Shared fields — non-null coverage old vs new

| field | old non-null % | new non-null % |
|---|---|---|
| `address` | 100.0% | 100.0% |
| `asset_type` | 100.0% | 100.0% |
| `auction_window` | 100.0% | 100.0% |
| `baths` | 99.9% | 99.9% |
| `beds` | 99.9% | 99.9% |
| `city` | 100.0% | 100.0% |
| `county` | 100.0% | 100.0% |
| `detail_url` | 100.0% | 100.0% |
| `est_value` | 65.4% | 64.7% |
| `event_code` | 100.0% | 100.0% |
| `financing_available` | 6.6% | 7.7% |
| `listing_id` | 100.0% | 100.0% |
| `lot_size` | 96.9% | 96.9% |
| `occupancy` | 100.0% | 100.0% |
| `primary_photo` | 100.0% | 100.0% |
| `product_type` | 100.0% | 100.0% |
| `pulled_at` | 100.0% | 100.0% |
| `source` | 100.0% | 100.0% |
| `sqft` | 97.0% | 97.1% |
| `state` | 100.0% | 100.0% |
| `status` | 100.0% | 100.0% |
| `status_group` | 100.0% | 100.0% |
| `street` | 100.0% | 100.0% |
| `structure_type` | 100.0% | 100.0% |
| `trustee_sale` | 100.0% | 100.0% |
| `year_built` | 97.0% | 97.2% |
| `zip` | 100.0% | 100.0% |
