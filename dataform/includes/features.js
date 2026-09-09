/**
 * Input features used by the boosted tree after TRANSFORM
 * (Amount is scaled inside the model; feature stats use the raw Amount).
 */
const PCA_FEATURES = Array.from({ length: 28 }, (_, i) => `V${i + 1}`);

const FEATURE_COLUMNS = [...PCA_FEATURES, "Amount", "Hour"];

const FEATURE_UNPIVOT_LIST = FEATURE_COLUMNS.join(", ");

const FEATURE_CAST_SELECT = FEATURE_COLUMNS.map(
  (name) => `CAST(${name} AS FLOAT64) AS ${name}`
).join(",\n    ");

/** SOP POL-SEC-2026-004 important monitoring variables (chapter 4.2). */
const AUDIT_FEATURES = ["V14", "V17", "V12"];

module.exports = {
  PCA_FEATURES,
  FEATURE_COLUMNS,
  FEATURE_UNPIVOT_LIST,
  FEATURE_CAST_SELECT,
  AUDIT_FEATURES,
};
