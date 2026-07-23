import type { SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement>

const baseProps: IconProps = {
  width: 16,
  height: 16,
  viewBox: '0 0 16 16',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.5,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
}

export function IconChat(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M13.5 7.5a5 5 0 0 1-5 5H5l-2.5 1 1-2.5a5 5 0 1 1 10-3.5Z" />
    </svg>
  )
}

export function IconHistory(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M3 4.5H.75V2.25" />
      <path d="M2.1 4.25A6 6 0 1 1 2 11.6" />
      <path d="M8 4.5V8l2.25 1.25" />
    </svg>
  )
}

export function IconTemplate(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <rect x="3.5" y="2" width="9" height="9" rx="1.5" />
      <path d="M2 5.5v6A2.5 2.5 0 0 0 4.5 14h6" />
    </svg>
  )
}

export function IconReport(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M2 13.5h12" />
      <rect x="3" y="8.5" width="2.5" height="3" rx=".5" />
      <rect x="6.75" y="5.5" width="2.5" height="6" rx=".5" />
      <rect x="10.5" y="2.5" width="2.5" height="9" rx=".5" />
    </svg>
  )
}

export function IconExpand(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M5.5 2H2v3.5M10.5 2H14v3.5M5.5 14H2v-3.5M10.5 14H14v-3.5" />
    </svg>
  )
}

export function IconStop(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <rect x="4" y="4" width="8" height="8" rx="1" fill="currentColor" stroke="none" />
    </svg>
  )
}

export function IconChevronRight(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="m6 3.5 4.5 4.5L6 12.5" />
    </svg>
  )
}

export function IconLogo(props: IconProps) {
  return (
    <svg {...baseProps} {...props} viewBox="0 0 18 16">
      <path d="M2 2h6" />
      <path d="M2 4.4h10" />
      <path d="M2 6.8h14" />
      <path d="M2 9.2h14" />
      <path d="M2 11.6h10" />
      <path d="M2 14h6" />
    </svg>
  )
}
