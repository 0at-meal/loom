/**
 * Phase 6 Static Baseline Reference Run Data
 *
 * Sourced directly from Phase 6 benchmark ledger (baseline_metrics.db).
 * 150 transactions evaluating StaticBaselineRouter against identical outage schedule:
 * - Tx 1-50: Warmup (Alpha = 1.0)
 * - Tx 51-53: Outage onset (Alpha = 1.0, absorbs M=3 failures)
 * - Tx 54-112: Circuit breaker tripped (Alpha = 0.0, 100% volume to Beta)
 * - Tx 113: Canary probe succeeds (Alpha = 1.0 snapback)
 * - Tx 114-150: Steady state recovery (Alpha = 1.0)
 */
export const BASELINE_REFERENCE_RUN = {
  "total_transactions": 150,
  "outage_trigger_index": 50,
  "recovery_trigger_index": 100,
  "transactions": [
    {
      "id": 1,
      "tx_id": "tx_baseline_warmup_1",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 2,
      "tx_id": "tx_baseline_warmup_2",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 3,
      "tx_id": "tx_baseline_warmup_3",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 4,
      "tx_id": "tx_baseline_warmup_4",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 5,
      "tx_id": "tx_baseline_warmup_5",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 6,
      "tx_id": "tx_baseline_warmup_6",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 7,
      "tx_id": "tx_baseline_warmup_7",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 8,
      "tx_id": "tx_baseline_warmup_8",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 9,
      "tx_id": "tx_baseline_warmup_9",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 10,
      "tx_id": "tx_baseline_warmup_10",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 11,
      "tx_id": "tx_baseline_warmup_11",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": false
    },
    {
      "id": 12,
      "tx_id": "tx_baseline_warmup_12",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 13,
      "tx_id": "tx_baseline_warmup_13",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 14,
      "tx_id": "tx_baseline_warmup_14",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 15,
      "tx_id": "tx_baseline_warmup_15",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 16,
      "tx_id": "tx_baseline_warmup_16",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 17,
      "tx_id": "tx_baseline_warmup_17",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 18,
      "tx_id": "tx_baseline_warmup_18",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": false
    },
    {
      "id": 19,
      "tx_id": "tx_baseline_warmup_19",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 20,
      "tx_id": "tx_baseline_warmup_20",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 21,
      "tx_id": "tx_baseline_warmup_21",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 22,
      "tx_id": "tx_baseline_warmup_22",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 23,
      "tx_id": "tx_baseline_warmup_23",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 24,
      "tx_id": "tx_baseline_warmup_24",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 25,
      "tx_id": "tx_baseline_warmup_25",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": false
    },
    {
      "id": 26,
      "tx_id": "tx_baseline_warmup_26",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 27,
      "tx_id": "tx_baseline_warmup_27",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 28,
      "tx_id": "tx_baseline_warmup_28",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 29,
      "tx_id": "tx_baseline_warmup_29",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 30,
      "tx_id": "tx_baseline_warmup_30",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 31,
      "tx_id": "tx_baseline_warmup_31",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 32,
      "tx_id": "tx_baseline_warmup_32",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 33,
      "tx_id": "tx_baseline_warmup_33",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 34,
      "tx_id": "tx_baseline_warmup_34",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 35,
      "tx_id": "tx_baseline_warmup_35",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 36,
      "tx_id": "tx_baseline_warmup_36",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 37,
      "tx_id": "tx_baseline_warmup_37",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 38,
      "tx_id": "tx_baseline_warmup_38",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 39,
      "tx_id": "tx_baseline_warmup_39",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 40,
      "tx_id": "tx_baseline_warmup_40",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 41,
      "tx_id": "tx_baseline_warmup_41",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 42,
      "tx_id": "tx_baseline_warmup_42",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 43,
      "tx_id": "tx_baseline_warmup_43",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 44,
      "tx_id": "tx_baseline_warmup_44",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 45,
      "tx_id": "tx_baseline_warmup_45",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 46,
      "tx_id": "tx_baseline_warmup_46",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 47,
      "tx_id": "tx_baseline_warmup_47",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 48,
      "tx_id": "tx_baseline_warmup_48",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 49,
      "tx_id": "tx_baseline_warmup_49",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 50,
      "tx_id": "tx_baseline_warmup_50",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 51,
      "tx_id": "tx_baseline_outage_1",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": false
    },
    {
      "id": 52,
      "tx_id": "tx_baseline_outage_2",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": false
    },
    {
      "id": 53,
      "tx_id": "tx_baseline_outage_3",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": false
    },
    {
      "id": 54,
      "tx_id": "tx_baseline_outage_4",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 55,
      "tx_id": "tx_baseline_outage_5",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 56,
      "tx_id": "tx_baseline_outage_6",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 57,
      "tx_id": "tx_baseline_outage_7",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": false
    },
    {
      "id": 58,
      "tx_id": "tx_baseline_outage_8",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 59,
      "tx_id": "tx_baseline_outage_9",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 60,
      "tx_id": "tx_baseline_outage_10",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 61,
      "tx_id": "tx_baseline_outage_11",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 62,
      "tx_id": "tx_baseline_outage_12",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 63,
      "tx_id": "tx_baseline_outage_13",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 64,
      "tx_id": "tx_baseline_outage_14",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": false
    },
    {
      "id": 65,
      "tx_id": "tx_baseline_outage_15",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 66,
      "tx_id": "tx_baseline_outage_16",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 67,
      "tx_id": "tx_baseline_outage_17",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 68,
      "tx_id": "tx_baseline_outage_18",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 69,
      "tx_id": "tx_baseline_outage_19",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 70,
      "tx_id": "tx_baseline_outage_20",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 71,
      "tx_id": "tx_baseline_outage_21",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 72,
      "tx_id": "tx_baseline_outage_22",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 73,
      "tx_id": "tx_baseline_outage_23",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 74,
      "tx_id": "tx_baseline_outage_24",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 75,
      "tx_id": "tx_baseline_outage_25",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 76,
      "tx_id": "tx_baseline_outage_26",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 77,
      "tx_id": "tx_baseline_outage_27",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 78,
      "tx_id": "tx_baseline_outage_28",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 79,
      "tx_id": "tx_baseline_outage_29",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 80,
      "tx_id": "tx_baseline_outage_30",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": false
    },
    {
      "id": 81,
      "tx_id": "tx_baseline_outage_31",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 82,
      "tx_id": "tx_baseline_outage_32",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 83,
      "tx_id": "tx_baseline_outage_33",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": false
    },
    {
      "id": 84,
      "tx_id": "tx_baseline_outage_34",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 85,
      "tx_id": "tx_baseline_outage_35",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 86,
      "tx_id": "tx_baseline_outage_36",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 87,
      "tx_id": "tx_baseline_outage_37",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 88,
      "tx_id": "tx_baseline_outage_38",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 89,
      "tx_id": "tx_baseline_outage_39",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 90,
      "tx_id": "tx_baseline_outage_40",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 91,
      "tx_id": "tx_baseline_outage_41",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": false
    },
    {
      "id": 92,
      "tx_id": "tx_baseline_outage_42",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 93,
      "tx_id": "tx_baseline_outage_43",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 94,
      "tx_id": "tx_baseline_outage_44",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 95,
      "tx_id": "tx_baseline_outage_45",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 96,
      "tx_id": "tx_baseline_outage_46",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 97,
      "tx_id": "tx_baseline_outage_47",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 98,
      "tx_id": "tx_baseline_outage_48",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 99,
      "tx_id": "tx_baseline_outage_49",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 100,
      "tx_id": "tx_baseline_outage_50",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": false
    },
    {
      "id": 101,
      "tx_id": "tx_baseline_recovery_1",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 102,
      "tx_id": "tx_baseline_recovery_2",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 103,
      "tx_id": "tx_baseline_recovery_3",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 104,
      "tx_id": "tx_baseline_recovery_4",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 105,
      "tx_id": "tx_baseline_recovery_5",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 106,
      "tx_id": "tx_baseline_recovery_6",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 107,
      "tx_id": "tx_baseline_recovery_7",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 108,
      "tx_id": "tx_baseline_recovery_8",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 109,
      "tx_id": "tx_baseline_recovery_9",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 110,
      "tx_id": "tx_baseline_recovery_10",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 111,
      "tx_id": "tx_baseline_recovery_11",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 112,
      "tx_id": "tx_baseline_recovery_12",
      "chosen_acquirer": "acquirer_beta",
      "alpha_weight": 0.0,
      "authorized": true
    },
    {
      "id": 113,
      "tx_id": "tx_baseline_recovery_13",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 114,
      "tx_id": "tx_baseline_recovery_14",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 115,
      "tx_id": "tx_baseline_recovery_15",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 116,
      "tx_id": "tx_baseline_recovery_16",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 117,
      "tx_id": "tx_baseline_recovery_17",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 118,
      "tx_id": "tx_baseline_recovery_18",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 119,
      "tx_id": "tx_baseline_recovery_19",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 120,
      "tx_id": "tx_baseline_recovery_20",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 121,
      "tx_id": "tx_baseline_recovery_21",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 122,
      "tx_id": "tx_baseline_recovery_22",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 123,
      "tx_id": "tx_baseline_recovery_23",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 124,
      "tx_id": "tx_baseline_recovery_24",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 125,
      "tx_id": "tx_baseline_recovery_25",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 126,
      "tx_id": "tx_baseline_recovery_26",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 127,
      "tx_id": "tx_baseline_recovery_27",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 128,
      "tx_id": "tx_baseline_recovery_28",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 129,
      "tx_id": "tx_baseline_recovery_29",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 130,
      "tx_id": "tx_baseline_recovery_30",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 131,
      "tx_id": "tx_baseline_recovery_31",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 132,
      "tx_id": "tx_baseline_recovery_32",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 133,
      "tx_id": "tx_baseline_recovery_33",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 134,
      "tx_id": "tx_baseline_recovery_34",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 135,
      "tx_id": "tx_baseline_recovery_35",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 136,
      "tx_id": "tx_baseline_recovery_36",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 137,
      "tx_id": "tx_baseline_recovery_37",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 138,
      "tx_id": "tx_baseline_recovery_38",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 139,
      "tx_id": "tx_baseline_recovery_39",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 140,
      "tx_id": "tx_baseline_recovery_40",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 141,
      "tx_id": "tx_baseline_recovery_41",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 142,
      "tx_id": "tx_baseline_recovery_42",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 143,
      "tx_id": "tx_baseline_recovery_43",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 144,
      "tx_id": "tx_baseline_recovery_44",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 145,
      "tx_id": "tx_baseline_recovery_45",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 146,
      "tx_id": "tx_baseline_recovery_46",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 147,
      "tx_id": "tx_baseline_recovery_47",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 148,
      "tx_id": "tx_baseline_recovery_48",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 149,
      "tx_id": "tx_baseline_recovery_49",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    },
    {
      "id": 150,
      "tx_id": "tx_baseline_recovery_50",
      "chosen_acquirer": "acquirer_alpha",
      "alpha_weight": 1.0,
      "authorized": true
    }
  ]
};

/**
 * Returns static baseline Alpha allocation weight for a given relative transaction offset
 * from outage trigger (delta_k = 0 at outage onset).
 */
export function getBaselineWeightAtOffset(deltaK) {
  if (deltaK < 0) return 1.0;
  if (deltaK < 3) return 1.0; // M=3 failures absorbed before trip
  if (deltaK < 63) return 0.0; // Tripped state
  return 1.0; // Recovered upon successful canary probe
}
