/**
 * AntD theme for the ReportAgent workbench.
 *
 * All component tokens here must be aligned with `src/styles/tokens.css`.
 * Do NOT introduce a separate color family for AntD — every visible
 * color must come from the design system, not from AntD defaults.
 */
import type { ThemeConfig } from 'antd'

// Design tokens (mirror of src/styles/tokens.css). Keep these in sync.
const T = {
  ink: '#10243e',
  ink2: '#293d53',
  muted: '#68798a',
  faint: '#95a19e',
  paper: '#fffefb',
  canvas: '#f3f3ee',
  rail: '#eaede9',
  line: '#dde2de',
  line2: '#cdd5d0',
  teal: '#087f73',
  tealDeep: '#06665e',
  tealSoft: '#dff2ed',
  tealPale: '#f2faf7',
  amber: '#b36c0d',
  amberSoft: '#fff0d6',
  red: '#b94a48',
  redSoft: '#fae7e4',
  green: '#23836f',
  // Typography
  fontDisplay: '"Songti SC", "STSong", "Noto Serif CJK SC", Georgia, serif',
  fontUi: '"Avenir Next", "PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans SC", sans-serif',
  fontMono: '"SFMono-Regular", Consolas, monospace',
} as const

const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: T.teal,
    colorPrimaryHover: T.tealDeep,
    colorPrimaryActive: T.tealDeep,
    colorPrimaryBg: T.tealSoft,
    colorPrimaryBgHover: T.tealPale,
    colorPrimaryBorder: T.teal,
    colorPrimaryText: T.tealDeep,

    colorSuccess: T.green,
    colorWarning: T.amber,
    colorError: T.red,
    colorInfo: T.teal,

    colorBgBase: T.canvas,
    colorBgLayout: T.canvas,
    colorBgContainer: T.paper,
    colorBgElevated: T.paper,
    colorFillSecondary: T.rail,
    colorFillTertiary: T.line,
    colorFillQuaternary: T.line2,

    colorBorder: T.line,
    colorBorderSecondary: T.line,
    colorSplit: T.line2,

    colorText: T.ink,
    colorTextSecondary: T.ink2,
    colorTextTertiary: T.muted,
    colorTextQuaternary: T.faint,

    borderRadius: 6,
    borderRadiusLG: 8,
    borderRadiusSM: 4,

    boxShadow: '0 8px 26px rgba(22,42,59,.07)',
    boxShadowSecondary: '0 4px 14px rgba(22,42,59,.05)',

    fontFamily: T.fontUi,
    fontSize: 13,
    fontSizeSM: 12,
    fontSizeLG: 15,
    lineHeight: 1.55,

    controlHeight: 32,
    controlHeightSM: 26,
    controlHeightLG: 38,

    paddingLG: 20,
    paddingMD: 14,
    paddingSM: 10,
    paddingXS: 6,
    marginLG: 22,
    marginMD: 14,
    marginSM: 10,
    marginXS: 6,
  },
  components: {
    Layout: {
      headerBg: T.ink,
      headerColor: '#FFFFFF',
      bodyBg: T.canvas,
      siderBg: T.rail,
      footerBg: T.canvas,
    },
    Card: {
      colorBgContainer: T.paper,
      colorBorderSecondary: T.line,
      paddingLG: 22,
      paddingMD: 18,
      boxShadow: '0 4px 14px rgba(22,42,59,.05)',
      headerBg: 'transparent',
      headerFontSize: 15,
    },
    Table: {
      headerBg: T.rail,
      headerColor: T.ink2,
      headerBorderRadius: 6,
      rowHoverBg: T.tealPale,
      borderColor: T.line,
      cellPaddingBlock: 10,
    },
    Menu: {
      itemColor: T.ink2,
      itemSelectedColor: T.tealDeep,
      itemHoverColor: T.ink,
      itemHoverBg: T.tealPale,
      itemSelectedBg: T.tealPale,
      itemActiveBg: T.tealPale,
      horizontalItemSelectedColor: T.tealDeep,
      horizontalItemHoverColor: T.ink,
      horizontalItemBorderRadius: 0,
      itemBorderRadius: 6,
    },
    Button: {
      borderRadius: 6,
      fontWeight: 500,
      boxShadow: 'none',
      primaryShadow: 'none',
      defaultShadow: 'none',
      defaultBorderColor: T.line,
      defaultColor: T.ink,
      defaultBg: T.paper,
      primaryColor: '#FFFFFF',
      fontSize: 13,
    },
    Input: {
      colorBorder: T.line,
      activeBorderColor: T.teal,
      hoverBorderColor: T.tealDeep,
      colorBgContainer: T.paper,
      activeShadow: 'none',
      paddingBlock: 6,
    },
    Select: {
      colorBorder: T.line,
      optionSelectedBg: T.tealSoft,
      optionActiveBg: T.tealPale,
      activeBorderColor: T.teal,
      hoverBorderColor: T.tealDeep,
      activeOutlineColor: 'transparent',
    },
    Tag: {
      colorBorder: T.line,
      defaultBg: T.rail,
      defaultColor: T.ink2,
    },
    Tooltip: {
      colorBgSpotlight: T.ink,
      colorTextLightSolid: '#FFFFFF',
    },
    Modal: {
      headerBg: T.paper,
      contentBg: T.paper,
      boxShadow: '0 18px 55px rgba(22,42,59,.09)',
    },
    Drawer: {
      colorBgElevated: T.paper,
    },
    Tabs: {
      inkBarColor: T.teal,
      itemSelectedColor: T.ink,
      itemHoverColor: T.ink,
    },
    Form: {
      labelColor: T.ink,
    },
    Skeleton: {
      color: T.line,
      colorGradientEnd: T.rail,
    },
    Message: {
      contentBg: T.paper,
      colorText: T.ink,
    },
    Notification: {
      colorBgElevated: T.paper,
    },
  },
}

export default antdTheme
export { T as designTokens }
