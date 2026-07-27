interface Props {
  size?: 'sm' | 'default' | 'lg'
  label?: string
  className?: string
}

export default function Spinner({ size = 'default', label, className }: Props) {
  const sizeClass = size !== 'default' ? ` atelier-spinner--${size}` : ''
  const spinner = (
    <span
      className={`atelier-spinner${sizeClass}${className ? ' ' + className : ''}`}
      role="status"
      aria-label={label || 'loading'}
    />
  )
  if (label) {
    return <span className="atelier-spinner-label">{spinner}{label}</span>
  }
  return spinner
}
