import { type InputHTMLAttributes, forwardRef } from 'react'

interface Props extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  error?: boolean
}

const TextField = forwardRef<HTMLInputElement, Props>(
  ({ error, className, ...rest }, ref) => {
    const cls = `atelier-textfield${error ? ' is-error' : ''}${className ? ' ' + className : ''}`
    return <input ref={ref} className={cls} {...rest} />
  },
)
TextField.displayName = 'TextField'

export default TextField
