import { forwardRef } from 'react'

interface Props {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  disabled?: boolean
  placeholder: string
}

/**
 * Floating bottom-anchored composer per the approved prototype:
 * exactly one input + one 发送 ↗ button, sticky over the scroll area.
 * Styling lives in styles/workbench.css (.wb-composer / .wb-send).
 */
const Composer = forwardRef<HTMLInputElement, Props>(function Composer(
  { value, onChange, onSubmit, disabled, placeholder },
  ref,
) {
  return (
    <form
      className="wb-composer"
      onSubmit={(event) => {
        event.preventDefault()
        if (!disabled) onSubmit()
      }}
    >
      <input
        ref={ref}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        autoComplete="off"
        aria-label="输入分析问题"
      />
      <button className="wb-send" type="submit" disabled={disabled || !value.trim()}>
        发送 ↗
      </button>
    </form>
  )
})

export default Composer
