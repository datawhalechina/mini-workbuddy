import { defineConfig } from 'vitepress'

// 部署基线：
// - GitHub Pages 项目站点，路径前缀固定为仓库名
// - 若在 EdgeOne 等根路径环境部署，设置环境变量 EDGEONE=1 即可切到 '/'
const isEdgeOne = process.env.EDGEONE === '1'
const baseConfig = isEdgeOne ? '/' : '/mini-workbuddy/'

export default defineConfig({
  lang: 'zh-CN',
  title: 'mini-workbuddy',
  description: '用 AgentScope 2.x 从零搭一个本地智能体工作台',
  base: baseConfig,
  srcExclude: ['**/README.md'],
  markdown: {
    math: true
  },
  themeConfig: {
    logo: '/logo.png',

    nav: [
      { text: '开始学习', link: '/01-我们究竟要造什么' },
      { text: '附录', link: '/A-附录' },
      { text: 'GitHub', link: 'https://github.com/datawhalechina/mini-workbuddy' },
    ],

    search: {
      provider: 'local',
      options: {
        translations: {
          button: { buttonText: '搜索文档', buttonAriaLabel: '搜索文档' },
          modal: {
            noResultsText: '没有找到相关内容',
            resetButtonTitle: '清除条件',
            footer: { selectText: '选择', navigateText: '切换' }
          }
        }
      }
    },

    sidebar: [
      {
        text: '第 0 篇 · 开场',
        items: [
          { text: '第 1 章　我们究竟要造什么', link: '/01-我们究竟要造什么' },
          { text: '第 2 章　读懂一次对话', link: '/02-读懂一次对话' },
        ]
      },
      {
        text: '第 1 篇 · 从会说话到会干活',
        items: [
          { text: '第 3 章　给它一双手', link: '/03-给它一双手' },
          { text: '第 4 章　解剖 ReAct 循环', link: '/04-解剖ReAct循环' },
          { text: '第 5 章　装进一个工作区', link: '/05-装进一个工作区' },
          { text: '第 6 章　工具不够用就自己造', link: '/06-工具不够用就自己造' },
        ]
      },
      {
        text: '第 2 篇 · 让它可靠',
        items: [
          { text: '第 7 章　别让它乱删东西', link: '/07-别让它乱删东西' },
          { text: '第 8 章　别乱猜，先问一句', link: '/08-别乱猜先问一句' },
          { text: '第 9 章　会话要活下去', link: '/09-会话要活下去' },
        ]
      },
      {
        text: '第 3 篇 · 做成真正可用的产品',
        items: [
          { text: '第 10 章　后端把事件流出去', link: '/10-后端把事件流出去' },
          { text: '第 11 章　前端把事件拼成界面', link: '/11-前端把事件拼成界面' },
          { text: '第 12 章　交互闭环', link: '/12-交互闭环' },
          { text: '第 13 章　日志、成本与可观测', link: '/13-日志成本与可观测' },
        ]
      },
      {
        text: '第 4 篇 · 收尾',
        items: [
          { text: '第 14 章　毕业设计：从 mini 到真的', link: '/14-毕业设计' },
        ]
      },
      {
        text: '附录',
        items: [
          { text: '附录 A　环境与模型接入速查', link: '/A-附录' },
          { text: '附录 B　事件类型全表', link: '/A-附录#附录-b-事件类型全表' },
          { text: '附录 C　章节索引', link: '/A-附录#附录-c-章节索引' },
        ]
      },
    ],

    socialLinks: [
      { icon: 'github', link: 'https://github.com/datawhalechina/mini-workbuddy' }
    ],

    editLink: {
      pattern: 'https://github.com/datawhalechina/mini-workbuddy/blob/main/docs/:path',
      text: '在 GitHub 上编辑此页'
    },

    outline: { level: [2, 3], label: '本页目录' },

    docFooter: { prev: '上一章', next: '下一章' },

    footer: {
      message: '教程正文采用 CC BY-NC-SA 4.0 许可，<code>code/</code> 下示例代码可自由使用',
      copyright: 'mini-workbuddy'
    }
  }
})
