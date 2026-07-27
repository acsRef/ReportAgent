interface RadioOption {
  value: string
  label: string
}

interface Props {
  options: RadioOption[]
  value: string | null
  onChange: (value: string) => void
  kind?: 'default' | 'pill'
}

export default function RadioGroup({ options, value, onChange, kind = 'default' }: Props) {
  if (kind === 'pill') {
    return (
      <div className="atelier-radio-group" role="radiogroup">
        {options.map((opt) => (
          <label
            key={opt.value}
            className="atelier-radio-pill"
          >
            <input
              type="radio"
              name="radio-group"
              value={opt.value}
              checked={value === opt.value}
              onChange={() => onChange(opt.value)}
            />
            {opt.label}
          </label>
        ))}
      </div>
    )
  }

  return (
    <div className="atelier-radio-group" role="radiogroup">
      {options.map((opt) => (
        <label key={opt.value} className="atelier-radio">
          <input
            type="radio"
            name="radio-group"
            value={opt.value}
            checked={value === opt.value}
            onChange={() => onChange(opt.value)}
          />
          {opt.label}
        </label>
      ))}
    </div>
  )
}
