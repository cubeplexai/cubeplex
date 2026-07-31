---
sidebar_position: 8
title: 单点登录
---

# 单点登录

CubePlex 可以把登录交给你的身份提供商（IdP），走 **SAML 2.0** 或 **OIDC**。成员用已有的账号登录，
访问权限在一处统一管理。

配置位于 **Admin > Authentication**（`/admin/authentication`）。每个组织一个连接。

:::info 企业版功能
SSO 需要企业版 license。没有的话这个页面显示"企业版功能"提示，见
[版本与授权](./editions.md)。Google 登录**不属于**这里——它在开源版里，无论有没有 license 都可用。
:::

:::info 📸 截图占位符
**截取内容：** Admin > Authentication，一个处于 testing 状态的 OIDC 连接，含状态标签、
Redirect URI 复制框和 Activate 按钮。
**资源：** `/img/admin/sso-connection.png`
:::

## 开始之前

配置过程中值要在两个方向搬，所以把 IdP 的管理台和 CubePlex 并排开着：

| 从 IdP 搬进 CubePlex | 从 CubePlex 搬进 IdP |
|---|---|
| OIDC：issuer URL、client ID、client secret | OIDC：**Redirect URI** |
| SAML：entity ID、SSO URL、签名证书 | SAML：**ACS URL** 和 **SP metadata URL** |

**CubePlex 那一侧的 URL 要从表单里复制，不要抄这篇文档。** 它们由你部署所配置的对外地址拼出来，
每个安装都不一样——IdP 填错了就会拒绝登录。

## 配置 OIDC

1. 进入 **Admin > Authentication**，点 **Configure SSO**。
2. 选 **OIDC**。
3. 填入 **Issuer URL** 后点 **Discover**。CubePlex 会读取该提供商的 `.well-known` 文档，
   自动填好授权、token 和 JWKS 端点。如果你的提供商不提供 discovery，手工填这几项。
4. 填入你在 IdP 里注册的应用的 **Client ID** 和 **Client Secret**。
5. 把表单里显示的 **Redirect URI** 复制到 IdP 的允许回调地址列表里。
6. 点 **Save**。连接会创建为 **testing** 状态，见[先测，再开](#先测再开)。

## 配置 SAML

1. 进入 **Admin > Authentication**，点 **Configure SSO**。
2. 选 **SAML**。
3. 填入 IdP 的 **Entity ID**、**SSO URL** 和**签名证书**（PEM）。
4. 把表单里的 **ACS URL** 给你的 IdP。如果它支持直接消费 SP metadata，就把
   **SP metadata URL** 给它——内容等价，而且这些值变了它仍然是对的。
5. 点 **Save**。

## 属性映射

CubePlex 需要每个人的邮箱和一个稳定标识。如果你的 IdP 用的是非标准的 claim / 属性名，
在 **Attribute mapping** 里做映射。

邮箱决定一次登录对应到哪个账号，所以映射错了要么产生重复账号，要么匹配不上已有账号。
显示名和头像是可选的；除非本人自己上传过头像，否则每次登录都会从 IdP 刷新。

## 先测，再开

新建的连接处于 **testing** 状态，这个状态的存在是有原因的：**连接处于 testing 期间，
所有人的密码登录照常可用。** 你可以和 IdP 走完一次真实往返、把问题改对，而不会把任何人锁在外面。

testing 期间，CubePlex 会记录最近一次登录尝试的原始属性，于是你能看到 IdP 到底发了什么，
再对着真实数据修正映射。

尝试登录之前先用 **Validate**。它检查所有不需要真人就能检查的东西：OIDC 查 discovery 文档、
JWKS 端点，以及你的 client ID 和 secret 是否被接受；SAML 查证书、IdP SSO URL 和 metadata URL。
client secret 填错会在这里暴露，而不是等到某次登录失败。

测试登录成功、映射出来的属性也对，就点 **Activate**。

## 激活之后会变什么

连接变成 **active** 后，该组织的 SSO 就是强制的：

- 成员用邮箱密码登录会被拒绝，并被指向该组织的 SSO 入口（`/login/<org-slug>`）。
- 登录页会为该组织显示 SSO 按钮。
- 如果开通策略是自动，首次登录的人可以被自动创建；如果是 **invite-only**，
  他必须已有账号且已是该组织成员。

这对组织里所有人生效，所以激活前请先读[如果 SSO 把你锁在外面](#如果-sso-把你锁在外面)。

## 开通策略

| 模式 | 没有 CubePlex 账号的人首次登录 |
|---|---|
| **自动（automatic）** | 自动创建账号并加入你的组织。 |
| **仅邀请（invite-only）** | 拒绝登录。先把人加进来，之后他才能登录。 |

invite-only 更严格：能进入你组织的人由你在 CubePlex 里维护，而不是由 IdP 恰好断言了什么决定。

## 已关联身份

**Linked identities** 表列出当前通过这个连接关联到 CubePlex 账号的外部账号。解除关联**不会**
删除 CubePlex 账号，只是断开对应关系——下次 SSO 登录会被当作首次登录，按你的开通策略重新关联。

## 如果 SSO 把你锁在外面

IdP 配错可能让每一个管理员都登不进来：SSO 拒绝他们，密码登录又被关掉了。恢复不走 UI，
因为 UI 正躲在那个坏掉的东西后面。在服务器上执行下面这条，CLI 可以直接访问数据库：

```bash
cubeplex admin disable-sso --org-slug <你的组织 slug>
```

它会把连接改回 inactive，密码登录立刻恢复。改好配置、测通，再重新激活。

```bash
cubeplex admin list-sso    # 列出每个组织的连接及当前状态
```

## SSO 还开着就卸掉企业版

卸掉企业版包**不会**清掉你的 SSO 配置——连接记录还在数据库里。处于这种状态的部署会把受影响的
组织搁死：SSO 已经没有东西能提供服务，而这些成员的密码登录仍然被拒绝。

CubePlex 会在启动时打一条 error，写明受影响的组织和恢复命令，并且**继续运行**，
这样其他组织不受影响。对日志里列出的每个组织执行 `disable-sso`，或者重新装上带有效 license 的包。

## Google 登录

Google 登录与企业版 SSO 是两件事，它在开源版里，配置在后端设置里而不是这个页面。

有一处交互值得知道：如果某个人属于一个 SSO 处于 **active** 的组织，那么他的 Google 登录也会被拒绝。
否则"强制 SSO"这条策略就可以被轻易绕过。
