interface Props {
  value: number
  onChange: (value: number) => void
  min?: number
  max?: number
}

export default function Stepper({ value, onChange, min = 1, max = 99 }: Props) {
  function decrement() {
    if (value > min) onChange(value - 1)
  }

  function increment() {
    if (value < max) onChange(value + 1)
  }

  return (
    <div className="atelier-stepper">
      <button
        className="atelier-stepper__btn"
        type="button"
        onClick={decrement}
        disabled={value <= min}
        aria-label="减少"
      >
        −
      </button>
      <input
        className="atelier-stepper__input"
        type="text"
        value={value}
        readOnly
        aria-label="当前值"
      />
      <button
        className="atelier-stepper__btn"
        type="button"
        onClick={increment}
        disabled={value >= max}
        aria-label="增加"
      >
        +
      </button>
    </div>
  )
}
