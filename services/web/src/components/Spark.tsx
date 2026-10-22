/** A labelled trend line: green if the latest value is at or above the one a year (4 quarters)
 * earlier, red if below. Nothing is drawn for fewer than two values. */
export function Spark({
  values,
  label,
  width = 46,
  height = 14,
}: {
  values: number[];
  label: string;
  width?: number;
  height?: number;
}) {
  if (values.length < 2) return <span className="spark-none" aria-hidden />;
  const lo = Math.min(...values);
  const range = Math.max(...values) - lo || 1;
  const points = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * width;
      const y = height - 1 - ((v - lo) / range) * (height - 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const last = values.at(-1)!;
  const yearAgo = values[Math.max(0, values.length - 5)]!;
  return (
    <svg
      className={last >= yearAgo ? "spark up" : "spark down"}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label}
    >
      <polyline points={points} fill="none" strokeWidth="1.3" />
    </svg>
  );
}
