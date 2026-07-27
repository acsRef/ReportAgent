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

export function IconLogout(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M6.5 2.5h-3v11h3" />
      <path d="M10.5 4.5 14 8l-3.5 3.5" />
      <path d="M14 8H6.5" />
    </svg>
  )
}

export function IconArrowLeft(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M13.5 8h-11" />
      <path d="M7 3.5 2.5 8 7 12.5" />
    </svg>
  )
}

export function IconTrash(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M2.5 4.5h11" />
      <path d="M5.5 4.5v-2h5v2" />
      <path d="M4 4.5 4.75 13.5h6.5L12 4.5" />
    </svg>
  )
}

export function IconPlus(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M8 3v10" />
      <path d="M3 8h10" />
    </svg>
  )
}

export function IconFile(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M9.5 2h-6v12h9V4.5L9.5 2Z" />
      <path d="M9.5 2v2.5H12" />
      <path d="M5.5 8h5M5.5 10.5h5" />
    </svg>
  )
}

export function IconClock(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <circle cx="8" cy="8" r="6" />
      <path d="M8 4.5V8l2.25 1.25" />
    </svg>
  )
}

export function IconMessage(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <rect x="2" y="3" width="12" height="8.5" rx="1.5" />
      <path d="m5.5 14 1.75-2.5h-2L5.5 14Z" />
      <path d="M5 6.5h6M5 8.75h4" />
    </svg>
  )
}

export function IconReload(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9" />
      <path d="M13.75 2v2.5h-2.5" />
    </svg>
  )
}

export function IconSave(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M2.5 2.5h9L13.5 4.5v9h-11v-11Z" />
      <path d="M5 2.5V6h5V2.5" />
      <rect x="5" y="8.5" width="6" height="3" />
    </svg>
  )
}

export function IconDownload(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M8 2.5v7.5" />
      <path d="m4.75 7 3.25 3.25L11.25 7" />
      <path d="M2.5 13.5h11" />
    </svg>
  )
}

export function IconLoading(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M8 2a6 6 0 1 1-6 6" />
    </svg>
  )
}

export function IconCheckCircle(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <circle cx="8" cy="8" r="6" />
      <path d="m5.25 8.25 1.75 1.75 3.75-4" />
    </svg>
  )
}

export function IconCloseCircle(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <circle cx="8" cy="8" r="6" />
      <path d="m6 6 4 4M10 6l-4 4" />
    </svg>
  )
}

export function IconMinusCircle(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <circle cx="8" cy="8" r="6" />
      <path d="M5.5 8h5" />
    </svg>
  )
}

export function IconSend(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M13.5 2.5 7 9" />
      <path d="M13.5 2.5 9.25 13.5 7 9l-4.5-2.25L13.5 2.5Z" />
    </svg>
  )
}

export function IconUser(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <circle cx="8" cy="5.5" r="2.75" />
      <path d="M2.75 13.5a5.25 5.25 0 0 1 10.5 0" />
    </svg>
  )
}

export function IconFund(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M2.5 13.5h11" />
      <path d="M4 10.5v1.5M7 8v4M10 5.5v6" />
      <path d="m3.5 6.5 3-2.5 2.5 2 3.5-3.5" />
    </svg>
  )
}

export function IconRise(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="m2.5 11.5 3.5-4 2.5 2 4.5-5" />
      <path d="M9.5 4.5H13V8" />
    </svg>
  )
}

export function IconTeam(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <circle cx="5.5" cy="5.5" r="2.25" />
      <circle cx="11" cy="6" r="1.75" />
      <path d="M1.75 13a3.75 3.75 0 0 1 7.5 0" />
      <path d="M10 13a2.75 2.75 0 0 1 4.25-2.3" />
    </svg>
  )
}

export function IconCart(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M2 2.5h2l1.75 8h6.5L14 5H5" />
      <circle cx="6.5" cy="13" r="1" />
      <circle cx="11.5" cy="13" r="1" />
    </svg>
  )
}

export function IconBulb(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M8 1.75a4.25 4.25 0 0 1 2.5 7.7c-.5.4-.75.9-.75 1.55h-3.5c0-.65-.25-1.15-.75-1.55A4.25 4.25 0 0 1 8 1.75Z" />
      <path d="M6.5 13h3M7 14.5h2" />
    </svg>
  )
}

export function IconArrowUp(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M8 13.5v-11" />
      <path d="M3.5 7 8 2.5 12.5 7" />
    </svg>
  )
}

export function IconArrowDown(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M8 2.5v11" />
      <path d="M3.5 9 8 13.5 12.5 9" />
    </svg>
  )
}

export function IconMinus(props: IconProps) {
  return (
    <svg {...baseProps} {...props}>
      <path d="M3 8h10" />
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
