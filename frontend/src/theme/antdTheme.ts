import type { ThemeConfig } from 'antd'

const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: '#1677ff',
    borderRadius: 6,
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif",
  },
  components: {
    Card: {
      paddingLG: 16,
    },
    Table: {
      headerBg: '#fafafa',
    },
  },
}

export default antdTheme
