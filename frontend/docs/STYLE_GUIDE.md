# Cubeplex Frontend 样式指南

> 本文档基于 cubetrace/frontend 的设计系统整理而成，所有前端开发需遵循此规范。

---

## 技术栈

| 工具                           | 说明                                              |
| ------------------------------ | ------------------------------------------------- |
| Tailwind CSS v4                | utility-first CSS 框架                            |
| CSS 变量 (HSL)                 | 主题系统基础                                      |
| Class-based 暗色模式           | `next-themes` 管理，`.light` 类切换亮色，默认暗色 |
| shadcn/ui                      | 基于 Radix UI 的无头 React 组件库                 |
| class-variance-authority (CVA) | 类型安全的样式变量管理                            |
| lucide-react                   | 图标库                                            |

---

## 主题系统

项目采用**暗色优先**策略，通过在 `<html>` 添加 `.light` 类切换亮色主题。

所有颜色必须使用 CSS 变量，禁止硬编码颜色值。

### CSS 变量定义

```css
/* 在 index.css 的 :root 中定义暗色主题（默认） */
:root {
  --background: 220 13% 9%; /* 深蓝灰 #121f2e */
  --foreground: 220 9% 95%; /* 极浅灰 #f0f4f8 */
  --card: 220 13% 11%; /* 卡片背景 #1a2839 */
  --card-foreground: 220 9% 95%;
  --popover: 220 13% 11%;
  --popover-foreground: 220 9% 95%;
  --primary: 210 100% 50%; /* 品牌蓝 #0080FF */
  --primary-foreground: 0 0% 100%;
  --secondary: 220 13% 13%; /* 深灰 #1f2d3d */
  --secondary-foreground: 220 9% 95%;
  --muted: 220 13% 13%;
  --muted-foreground: 220 9% 65%; /* 次要文字 */
  --accent: 220 13% 15%;
  --accent-foreground: 220 9% 95%;
  --destructive: 0 62% 50%; /* 警告红 #f01b1b */
  --destructive-foreground: 0 0% 100%;
  --border: 220 13% 15%;
  --input: 220 13% 15%;
  --ring: 210 100% 50%; /* 焦点环 = primary */
  --radius: 0.5rem; /* 基础圆角 8px */
}

/* 亮色主题 */
.light {
  --background: 0 0% 100%;
  --foreground: 240 10% 3.9%;
  --card: 0 0% 100%;
  --card-foreground: 240 10% 3.9%;
  --primary: 210 100% 50%; /* 保持品牌蓝一致 */
  --primary-foreground: 0 0% 100%;
  --secondary: 240 4.8% 95.9%;
  --secondary-foreground: 240 5.9% 10%;
  --muted: 240 4.8% 95.9%;
  --muted-foreground: 240 3.8% 46.1%;
  --accent: 240 4.8% 95.9%;
  --destructive: 0 84.2% 60.2%;
  --border: 240 5.9% 90%;
  --input: 240 5.9% 90%;
  --ring: 210 100% 50%;
}
```

### 使用规范

```tsx
// ✅ 正确 - 使用 Tailwind 语义类
<div className="bg-background text-foreground">
<div className="bg-card border border-border">
<button className="bg-primary text-primary-foreground">

// ❌ 错误 - 硬编码颜色
<div style={{ background: '#121f2e' }}>
<div className="bg-gray-900">
```

---

## 颜色系统

### 品牌色

| 名称        | 值                                              | 用途                       |
| ----------- | ----------------------------------------------- | -------------------------- |
| Primary     | `hsl(210, 100%, 50%)` → #0080FF                 | 主按钮、焦点环、链接、强调 |
| Destructive | 暗: `hsl(0, 62%, 50%)` / 亮: `hsl(0, 84%, 60%)` | 删除、错误、警告           |

### 功能色板（语义标签）

用于标记不同类型的内容标签，五色系：

| 类型             | 暗色主题                | 亮色主题 |
| ---------------- | ----------------------- | -------- |
| task（任务）     | #60a5fa（Sky Blue 400） | #2563eb  |
| chat（对话）     | #10b981（Emerald 500）  | #059669  |
| workflow（流程） | #ec4899（Pink 500）     | #db2777  |
| tool（工具）     | #f97316（Orange 500）   | #ea580c  |
| root（根节点）   | #06b6d4（Cyan 500）     | #0e7490  |

背景统一为对应颜色的 `rgba(..., 0.1)`（暗色）或 `rgba(..., 0.08)`（亮色）。

### JSON 语法高亮色

```css
.json-key:     #f472b6   /* 粉色 - 键名 */
.json-string:  #34d399   /* 绿色 - 字符串值 */
.json-number:  #fbbf24   /* 黄色 - 数字 */
.json-boolean: #60a5fa   /* 蓝色 - 布尔值 */
.json-null:    #9ca3af   /* 灰色 - null */
```

---

## 字体系统

### 字族

```css
/* 正文字体 */
font-family:
  'Inter',
  -apple-system,
  BlinkMacSystemFont,
  'Segoe UI',
  Roboto,
  sans-serif;
/* 导入 */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

/* 代码字体 */
font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
```

### 字重规范

| 权重 | class           | 用途             |
| ---- | --------------- | ---------------- |
| 300  | `font-light`    | 辅助信息、时间戳 |
| 400  | `font-normal`   | 正文             |
| 500  | `font-medium`   | 标签、次级标题   |
| 600  | `font-semibold` | 卡片标题、导航   |
| 700  | `font-bold`     | 页面大标题       |

### 渲染优化

```css
body {
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  font-feature-settings:
    'rlig' 1,
    'calt' 1;
}
```

---

## 间距系统

遵循 Tailwind 默认 4px 基准网格。

### 常用间距

| 用途               | class                 |
| ------------------ | --------------------- |
| 组件内边距（紧凑） | `p-2` / `px-3 py-1`   |
| 组件内边距（标准） | `p-4` / `px-4 py-2`   |
| 卡片内边距         | `p-4` / `p-6`         |
| 页面内边距         | `px-6 py-4`           |
| 元素间距（紧）     | `gap-2` / `space-x-2` |
| 元素间距（标准）   | `gap-4` / `space-x-4` |
| 区块间距           | `gap-6` / `space-y-6` |

---

## 圆角系统

基础变量 `--radius: 0.5rem`（8px）。

```
rounded-sm  → calc(var(--radius) - 4px) = 4px  // 标签、badge
rounded-md  → calc(var(--radius) - 2px) = 6px  // 输入框、按钮
rounded-lg  → var(--radius) = 8px              // 卡片
rounded-full → 50%                              // 头像、圆形按钮
```

---

## 组件规范

### shadcn/ui 组件使用

所有 UI 组件从 `@/components/ui/` 导入，遵循 shadcn/ui 官方文档。

#### Button 按钮

```tsx
import { Button } from '@/components/ui/button'

// 主按钮（默认）
<Button>操作</Button>

// 次级按钮
<Button variant="secondary">取消</Button>

// 幽灵按钮（透明背景）
<Button variant="ghost">更多</Button>

// 尺寸
<Button size="sm">小</Button>
<Button size="lg">大</Button>

// 图标按钮
<Button size="icon"><Trash2 className="size-4" /></Button>

// 禁用 + 加载状态
<Button disabled>禁用</Button>
<Button><Loader2 className="animate-spin size-4 mr-2" />加载中</Button>
```

#### Input 输入框

```tsx
import { Input } from '@/components/ui/input'
;<Input placeholder="输入内容..." className="max-w-sm" />
```

#### Textarea 文本域

```tsx
import { Textarea } from '@/components/ui/textarea'
;<Textarea placeholder="输入多行内容..." rows={4} className="resize-none" />
```

#### Scroll Area 滚动容器

```tsx
import { ScrollArea } from '@/components/ui/scroll-area'
;<ScrollArea className="h-[200px] w-full border rounded-lg p-4">{/* 长内容 */}</ScrollArea>
```

#### Badge 标签

```tsx
import { Badge } from '@/components/ui/badge'

<Badge>标签</Badge>
<Badge variant="secondary">次级</Badge>
<Badge variant="destructive">删除</Badge>
```

#### Collapsible 展开折叠

```tsx
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from '@/components/ui/collapsible'
import { ChevronDown } from 'lucide-react'
;<Collapsible>
  <CollapsibleTrigger className="flex items-center gap-2">
    <ChevronDown className="h-4 w-4" />
    展开详情
  </CollapsibleTrigger>
  <CollapsibleContent className="mt-2">{/* 折叠内容 */}</CollapsibleContent>
</Collapsible>
```

#### Tooltip 提示

```tsx
import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip'
;<Tooltip>
  <TooltipTrigger>hover me</TooltipTrigger>
  <TooltipContent>提示文字</TooltipContent>
</Tooltip>
```

#### Separator 分割线

```tsx
import { Separator } from '@/components/ui/separator'
;<div className="space-y-4">
  <div>内容</div>
  <Separator />
  <div>内容</div>
</div>
```

### 自定义布局组件

这些组件基于 shadcn/ui 但为项目定制。

#### AppShell 应用框架

```tsx
import { AppShell } from '@/components/layout/AppShell'
;<AppShell>{/* 内容 */}</AppShell>
```

#### Sidebar 侧边栏

```tsx
import { Sidebar } from '@/components/layout/Sidebar'
;<Sidebar />
```

#### InputBar 输入栏

```tsx
import { InputBar } from '@/components/layout/InputBar'

// 欢迎页（创建对话）
<InputBar onSubmit={handleCreateConversation} />

// 聊天页（发送消息）
<InputBar conversationId={id} />
```

#### ExecutionDetails 执行细节

```tsx
import { ExecutionDetails } from '@/components/chat/ExecutionDetails'
import type { AgentEvent } from '@cubeplex/core'
;<ExecutionDetails events={agentEvents} isStreaming={false} />
```

### 卡片 / 内容容器

使用原生 div 和 shadcn/ui 的样式指导。

```tsx
// 标准卡片
<div className="bg-card border border-border rounded-lg p-4">
  {/* 内容 */}
</div>

// 带标题卡片
<div className="bg-card border border-border rounded-lg overflow-hidden">
  <div className="border-b border-border bg-muted/50 px-6 py-4">
    <h3 className="font-semibold text-foreground">标题</h3>
  </div>
  <div className="p-4">
    {/* 内容 */}
  </div>
</div>
```

---

## 动画与过渡

### 原则

- **只在有意义的交互上加动画**，避免滥用
- 优先使用 CSS transition，复杂动画才用 @keyframes
- 时长控制在 150ms-300ms，超出会显迟钝

### 标准过渡

```css
transition-colors   /* 150ms - 颜色变化（hover、focus） */
transition-shadow   /* hover 阴影加深 */
transition-all      /* 200ms - 组合属性变化 */

/* 宽度/尺寸变化（如进度条） */
transition: width 0.3s ease-in-out;
```

### 加载状态

```tsx
// 旋转加载
<Loader2 className="animate-spin h-4 w-4" />

// 脉冲指示灯
<span className="animate-pulse h-2 w-2 rounded-full bg-primary" />
```

### 主题切换图标动画

```tsx
// 太阳图标（暗色时隐藏）
<Sun className="rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
// 月亮图标（亮色时隐藏）
<Moon className="absolute rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
```

---

## 滚动条样式

```css
/* 全局滚动条 */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}
::-webkit-scrollbar-track {
  background: hsl(var(--background));
}
::-webkit-scrollbar-thumb {
  background: hsl(var(--border));
  border-radius: 4px;
}

/* 面板内滚动条（更细） */
.panel-scroll::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
.panel-scroll::-webkit-scrollbar-thumb {
  background: hsl(var(--muted-foreground) / 0.3);
  border-radius: 3px;
  transition: all 0.2s ease;
}
.panel-scroll::-webkit-scrollbar-thumb:hover {
  background: hsl(var(--muted-foreground) / 0.5);
}
```

---

## 响应式布局

### 断点

| 前缀  | 最小宽度 |
| ----- | -------- |
| `sm:` | 640px    |
| `md:` | 768px    |
| `lg:` | 1024px   |
| `xl:` | 1280px   |

### 常见布局模式

```tsx
// 列表网格：移动1列 → 平板3列 → 桌面4列
<div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-4 gap-4">

// 主从面板：移动全宽 → 桌面分栏
<div className="flex flex-col md:flex-row gap-4">
  <div className="w-full md:w-[40%]">左侧面板</div>
  <div className="w-full md:w-[60%]">右侧内容</div>
</div>

// 全屏高度布局（减去导航栏）
<div className="h-[calc(100vh-73px)] overflow-hidden">
```

---

## 可访问性规范

shadcn/ui 基于 Radix UI，已内置 ARIA 属性和键盘导航支持。开发时遵循：

- **shadcn/ui 组件自动提供可访问性**，无需额外配置（Button、Input 等）
- 图标按钮必须有 `aria-label`：
  ```tsx
  <Button size="icon" aria-label="删除">
    <Trash2 className="size-4" />
  </Button>
  ```
- 纯装饰性图标加 `aria-hidden="true"`：
  ```tsx
  <ArrowRight className="size-4" aria-hidden="true" />
  ```
- 表单输入与标签关联（shadcn/ui Input 已支持）：
  ```tsx
  <label htmlFor="email">邮箱</label>
  <Input id="email" type="email" />
  ```
- 使用 `sr-only` 为屏幕阅读器提供额外上下文（Tailwind 内置）：
  ```tsx
  <span className="sr-only">（可选）</span>
  ```
- 焦点环已通过 CSS 变量 `--ring` 应用，shadcn/ui 按钮自动管理

---

## 禁止事项

| 禁止                                         | 原因                                                      |
| -------------------------------------------- | --------------------------------------------------------- |
| 硬编码 `#hex` 或 `rgb()` 颜色                | 主题切换失效                                              |
| 使用 `dark:` 前缀（Tailwind class strategy） | 项目用 class 策略 + next-themes，应用 `.light` 类切换亮色 |
| 任意 `gray-*`、`blue-*` 等 Tailwind 原始色   | 破坏主题一致性，用语义变量替代                            |
| 未使用 CSS 变量的新颜色                      | 无法主题扩展                                              |
| 超过 300ms 的动画                            | 体验迟钝                                                  |
| Arial、Roboto、System UI 等通用字体          | 使用 Inter + 代码用 Monaco                                |
| 复制 shadcn/ui 组件代码到其他目录            | 维护成本高，统一在 `components/ui/` 管理                  |
| 跳过 shadcn/ui 内置的 ARIA/键盘支持          | 破坏可访问性，重新造轮子                                  |

---

## 快速参考：视觉层次

```
第1层 background   极深蓝灰 / 纯白
第2层 card         深灰 / 白（略有边框分隔）
第3层 muted        次级内容区背景
第4层 foreground   主文字（高对比）
第5层 muted-foreground  辅助文字（低对比）
第6层 primary      品牌蓝 - 最强调用
第7层 accent       hover 反馈层
```

---

## shadcn/ui 开发工作流

### 添加新组件

```bash
pnpm dlx shadcn@latest add <component-name>
```

示例：

```bash
# 添加 Dialog 组件
pnpm dlx shadcn@latest add dialog

# 添加多个组件
pnpm dlx shadcn@latest add button checkbox dropdown-menu
```

### 使用组件

所有 shadcn/ui 组件从 `@/components/ui/` 导入：

```tsx
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
```

### 自定义组件样式

使用 `cn()` 工具函数合并 Tailwind 类（已在 `lib/utils.ts` 提供）：

```tsx
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

export function CustomButton({ className, ...props }) {
  return <Button className={cn('custom-class', className)} {...props} />
}
```

### 关键文件

- `components/ui/` — shadcn/ui 组件（自动生成）
- `lib/utils.ts` — `cn()` 工具函数 + 辅助方法
- `components/layout/` — 自定义布局组件（AppShell、Sidebar 等）
- `components/chat/` — 业务组件（消息、执行细节等）
- `hooks/` — React hooks（useMessages、useConversations 等）
