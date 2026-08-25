// Inline SVG Beta(alpha, beta) density curve. No chart library — per P4
// scope, curves are hand-drawn: compute 100 points of
// f(x) ∝ x^(alpha-1) * (1-x)^(beta-1), normalize by the max, plot as an SVG
// path.

function betaDensityPoints(alpha, beta, n = 100) {
  const raw = []
  let max = 0
  for (let i = 0; i <= n; i++) {
    const x = i / n
    // clamp away from the exact 0/1 boundary so alpha/beta < 1 (which blow
    // up at the edges) still produce a finite, plottable value
    const xx = Math.min(Math.max(x, 1e-6), 1 - 1e-6)
    const y = xx ** (alpha - 1) * (1 - xx) ** (beta - 1)
    raw.push({ x, y })
    if (Number.isFinite(y) && y > max) max = y
  }
  return raw.map((p) => ({ x: p.x, y: max > 0 ? p.y / max : 0 }))
}

export default function BetaCurve({ alpha, beta, width = 260, height = 110, color = 'var(--accent)' }) {
  const points = betaDensityPoints(alpha, beta, 100)
  const padX = 6
  const padY = 8
  const w = width - padX * 2
  const h = height - padY * 2

  const linePath = points
    .map((p, i) => {
      const px = padX + p.x * w
      const py = padY + (1 - p.y) * h
      return `${i === 0 ? 'M' : 'L'}${px.toFixed(2)},${py.toFixed(2)}`
    })
    .join(' ')
  const areaPath = `${linePath} L${(padX + w).toFixed(2)},${(padY + h).toFixed(2)} L${padX.toFixed(2)},${(padY + h).toFixed(2)} Z`

  const mean = alpha / (alpha + beta)
  const meanX = padX + mean * w

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`Beta(${alpha.toFixed(2)}, ${beta.toFixed(2)}) density curve, mean ${mean.toFixed(2)}`}
      className="beta-curve"
    >
      <line x1={padX} y1={padY + h} x2={padX + w} y2={padY + h} className="beta-curve-axis" />
      <path d={areaPath} fill={color} opacity="0.16" stroke="none" />
      <path d={linePath} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      <line x1={meanX} y1={padY} x2={meanX} y2={padY + h} stroke={color} strokeWidth="1" strokeDasharray="3,3" opacity="0.7" />
    </svg>
  )
}
