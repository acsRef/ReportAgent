import type { ThemeConfig } from 'antd'

const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: '#1E40AF',
    colorSuccess: '#059669',
    colorWarning: '#D97706',
    colorError: '#DC2626',
    colorInfo: '#3B82F6',
    colorBgLayout: '#F8FAFC',
    colorBgContainer: '#FFFFFF',
    colorBgElevated: '#FFFFFF',
    colorBorder: '#E2E8F0',
    colorBorderSecondary: '#DBEAFE',
    colorText: '#1E293B',
    colorTextSecondary: '#64748B',
    colorTextTertiary: '#94A3B8',
    borderRadius: 8,
    borderRadiusLG: 12,
    boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)',
    boxShadowSecondary: '0 4px 6px rgba(0,0,0,0.05), 0 2px 4px rgba(0,0,0,0.06)',
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans SC', sans-serif",
    fontSize: 13,
    fontSizeSM: 12,
    fontSizeLG: 15,
    controlHeight: 34,
    controlHeightSM: 28,
    paddingLG: 20,
    paddingMD: 16,
    paddingSM: 12,
    paddingXS: 8,
    marginLG: 20,
    marginMD: 16,
    marginSM: 12,
    marginXS: 8,
  },
  components: {
    Layout: {
      headerBg: '#0F172A',
      bodyBg: '#F8FAFC',
    },
    Card: {
      paddingLG: 20,
      paddingMD: 16,
      colorBorderSecondary: '#E2E8F0',
    },
    Table: {
      headerBg: '#F8FAFC',
      headerColor: '#64748B',
      headerBorderRadius: 8,
      rowHoverBg: '#F1F5F9',
      borderColor: '#F1F5F9',
    },
    Menu: {
      itemColor: 'rgba(255,255,255,0.65)',
      itemSelectedColor: '#FFFFFF',
      itemHoverBg: 'rgba(255,255,255,0.08)',
      itemActiveBg: 'rgba(255,255,255,0.12)',
      horizontalItemSelectedColor: '#FFFFFF',
      horizontalItemBorderRadius: 6,
    },
    Button: {
      boxShadow: 'none',
      primaryShadow: 'none',
    },
    Input: {
      colorBorder: '#E2E8F0',
      activeBorderColor: '#3B82F6',
      hoverBorderColor: '#3B82F6',
      colorBgContainer: '#FFFFFF',
    },
    Select: {
      colorBorder: '#E2E8F0',
      optionSelectedBg: '#EFF6FF',
    },
    Tag: {
      colorBorder: '#DBEAFE',
    },
    Tooltip: {
      colorBgSpotlight: '#1E293B',
    },
    Modal: {
      headerBg: '#FFFFFF',
      contentBg: '#FFFFFF',
    },
    Tabs: {
      inkBarColor: '#1E40AF',
      itemSelectedColor: '#1E40AF',
      itemHoverColor: '#3B82F6',
    },
  },
}

export default antdTheme