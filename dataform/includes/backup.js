/**
 * Snapshot a Looker Studio daily table into v_detection_* before CREATE OR REPLACE.
 *
 * The source is a fully-qualified name, not ref(), so Dataform does not rebuild
 * the daily table first. Pair each snapshot action with
 * dependencies: ["v_detection_..."] on the matching daily table.
 *
 * @param {string} sourceTableName short table name, e.g. ulb_fraud_detection_evaluation
 * @param {string} projectId GCP project
 * @param {string} dataset BigQuery dataset
 * @param {string} targetFq Dataform self() for the backup table
 * @returns {string} BigQuery script
 */
function snapshotSql(sourceTableName, projectId, dataset, targetFq) {
  const informationSchema = `\`${projectId}.${dataset}.INFORMATION_SCHEMA.TABLES\``;
  const sourceFq = `\`${projectId}.${dataset}.${sourceTableName}\``;

  return `
-- ${sourceTableName} が無い初回は何もしない（CREATE OR REPLACE 後の翌日以降から履歴が貯まる）
BEGIN
  DECLARE source_exists BOOL DEFAULT FALSE;

  SET source_exists = (
    SELECT COUNT(*) > 0
    FROM ${informationSchema}
    WHERE table_name = "${sourceTableName}"
  );

  IF source_exists THEN
    CREATE TABLE IF NOT EXISTS ${targetFq} AS
    SELECT
      *
    FROM
      ${sourceFq};

    INSERT INTO ${targetFq}
    SELECT
      src.*
    FROM
      ${sourceFq} AS src
    WHERE
      NOT EXISTS (
        SELECT
          1
        FROM
          ${targetFq} AS dst
        WHERE
          dst.evaluation_date = src.evaluation_date
      );
  END IF;
END;
`;
}

module.exports = {
  snapshotSql,
};
