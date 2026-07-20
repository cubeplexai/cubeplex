# cubeplex 部署手册

一条 `helm upgrade --install` 命令即可将 cubeplex（backend + frontend +
Postgres + Redis + rustfs，可选 alibaba OpenSandbox 全家桶）部署到已有的
Kubernetes 集群中。

**完整的、持续维护的安装指南在文档站点上：**
[cubeplex.ai/docs/zh-Hans/deployment/kubernetes](https://cubeplex.ai/docs/zh-Hans/deployment/kubernetes)
（English: [cubeplex.ai/docs/deployment/kubernetes](https://cubeplex.ai/docs/deployment/kubernetes)）

内容涵盖前置依赖、部署架构、构建并推送镜像、逐字段撰写
`values.local.yaml`、安装、部署后验证、常见故障排查，以及完整的配置项
参考。

本目录保存指南中用到的 chart、脚本和模板：`charts/cubeplex/`、
`scripts/{build-and-push,helm-install,smoke-test,e2e}.sh`。快速开始见
[README.md](README.md)。chart 设计与决策见
[docs/dev/specs/2026-06-10-helm-deploy-design.md](../../docs/dev/specs/2026-06-10-helm-deploy-design.md)。
