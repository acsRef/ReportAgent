const RADIUS = 26
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

interface Props {
  percent: number
}

export default function ProgressCircle({ percent }: Props) {
  const clamped = Math.max(0, Math.min(100, percent))
  const offset = CIRCUMFERENCE - (clamped / 100) * CIRCUMFERENCE

  return (
    <div className="atelier-progress-circle">
      <svg viewBox="0 0 64 64" width="64" height="64">
        <circle className="atelier-progress-circle__track" r={RADIUS} cx="32" cy="32" />
        <circle
          className="atelier-progress-circle__fill"
          r={RADIUS}
          cx="32"
          cy="32"
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={offset}
        />
      </svg>
      <span className="atelier-progress-circle__text">{clamped}%</span>
    </div>
  )
}
