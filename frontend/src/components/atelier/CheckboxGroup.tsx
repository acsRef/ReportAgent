interface CheckboxOption {
  value: string
  label: string
}

interface Props {
  options: CheckboxOption[]
  value: string[]
  onChange: (value: string[]) => void
}

export default function CheckboxGroup({ options, value, onChange }: Props) {
  function handleToggle(optValue: string, checked: boolean) {
    if (checked) {
      onChange([...value, optValue])
    } else {
      onChange(value.filter((v) => v !== optValue))
    }
  }

  return (
    <div className="atelier-checkbox-group">
      {options.map((opt) => (
        <label key={opt.value} className="atelier-checkbox">
          <input
            type="checkbox"
            checked={value.includes(opt.value)}
            onChange={(e) => handleToggle(opt.value, e.target.checked)}
          />
          {opt.label}
        </label>
      ))}
    </div>
  )
}
