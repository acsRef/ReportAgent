import type { ReactNode } from 'react'

interface Props {
  icon?: ReactNode
  title?: string
  description?: string | ReactNode
  action?: ReactNode
  className?: string
  style?: React.CSSProperties
}

export default function Empty({ icon, title, description, action, className, style }: Props) {
  return (
    <div className={`atelier-empty${className ? ' ' + className : ''}`} style={style}>
      {icon && <div className="atelier-empty__icon">{icon}</div>}
      {title && <div className="atelier-empty__title">{title}</div>}
      {description && <div className="atelier-empty__desc">{description}</div>}
      {action && <div className="atelier-empty__action">{action}</div>}
    </div>
  )
}
