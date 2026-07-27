import { type TextareaHTMLAttributes, forwardRef } from 'react'

interface Props extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  error?: boolean
}

const TextArea = forwardRef<HTMLTextAreaElement, Props>(
  ({ error, className, ...rest }, ref) => {
    const cls = `atelier-textarea${error ? ' is-error' : ''}${className ? ' ' + className : ''}`
    return <textarea ref={ref} className={cls} {...rest} />
  },
)
TextArea.displayName = 'TextArea'

export default TextArea
