/**
 * Looker Studio 評価テーブルの日次スナップショット履歴。
 *
 * 入力の Batch は当日スライスの WRITE_TRUNCATE のため、再計算できるのは当日だけ。
 * incremental 実行時は当日パーティションを消してから INSERT し、過去日は残す。
 * 同一日の再実行はべき等。protected: true で full-refresh による履歴削除を防ぐ。
 */
const EVALUATION_DATE_EXPR = 'CURRENT_DATE("Asia/Tokyo")';

function deleteTodaySnapshot(tableRef) {
  return `DELETE FROM ${tableRef} WHERE evaluation_date = ${EVALUATION_DATE_EXPR}`;
}

function isToday(alias) {
  const column = alias ? `${alias}.evaluation_date` : "evaluation_date";
  return `${column} = ${EVALUATION_DATE_EXPR}`;
}

module.exports = {
  EVALUATION_DATE_EXPR,
  deleteTodaySnapshot,
  isToday,
};
