# RedLedger Clean Architecture (纯净多群高并发记账系统)

RedLedger 是专为高并发微信群记账场景打造的现代化、低延迟流式计算引擎。

本项目彻底重构了早期单体架构，采用**独立微服务集群架构**（`Ingress Router` + `Group Worker Micro-Engines`），彻底消除数据库写锁争用、死锁与跨群卡顿问题。

---

## 核心设计与架构特点

### 1. 多群进程隔离集群架构 (`redledger-multi`)
- **Ingress Router (网关路由器)**：
  - 单一对外接入端口（默认 8766），负责接收来自 Hook 或数据库轮询桥接的实时消息。
  - 基于动态路由表（`routes.json`）毫秒级智能路由分发，将不同微信群的消息分流给独立的群 Worker。
  - 单群卡顿或异常完全隔离，互不影响。
- **Group Worker Micro-Engines (单群微引擎)**：
  - 每个微信群独占独立进程、独立内存引擎、以及独立 SQLite 数据文件。
  - 彻底规避全局锁争用，支持热加载与单群平滑重启。
- **Cluster Manager (集群管控器)**：
  - 一键拉起或停止整套集群（`start-cluster.bat` / `stop-cluster.bat`），具备子进程健康守护与自动恢复能力。

### 2. 纯内存零阻塞结算内核 (`redledger-slim/core`)
- **纯内存流式计算 (`CoreEngine`)**：
  - 下注解析、算力撮合、水钱抽水、庄家盈亏全部在内存模型中执行，单局结算时间 **< 0.2ms**。
  - 数据库操作全异步入队，不阻塞实时交易主线程。
- **报告异步渲染与双通道分发 (`redledger-slim/pipeline`)**：
  - 结算报表使用高精度 Pillow 图像渲染器（`SlimRenderer`），渲染耗时仅 **~70ms**。
  - 支持 **报表图片 + 宝路文本（`宝路：{route}`）** 自动双通道下发（VXHook 优先，原生 GUI 兜底），零等待、高稳定性。

---

## 目录结构

```text
redledger/
├── redledger-multi/                # 多群集群架构组件
│   ├── router.py                   # Ingress 网关路由器 (Port 8766)
│   ├── worker.py                   # 独立群 Worker 微引擎
│   ├── cluster_manager.py          # 集群进程监控与生命周期守护
│   ├── routes.json                 # 动态群路由映射表
│   ├── start-cluster.bat           # 一键启动脚本
│   └── stop-cluster.bat            # 一键停止脚本
│
├── redledger-slim/                 # 核心纯净业务引擎
│   ├── core/                       # 核心业务逻辑
│   │   ├── engine.py               # 纯内存高速记账引擎
│   │   ├── rules.py                # 下注语法解析与赔率水钱算法
│   │   └── route.py                # 宝路计算与合并算法
│   ├── gateway/                    # 接入网关
│   │   ├── server.py               # 业务网关与结算流程驱动
│   │   ├── vxhook.py               # VXHook 协议桥接
│   │   └── local_source.py         # 微信本地数据库快照监听桥接
│   ├── pipeline/                   # 分发与渲染管道
│   │   ├── dispatcher.py           # 异步公平队列分发器
│   │   ├── report_image.py         # 高清账单报表渲染器
│   │   └── wechat_desktop_sender.py# 原生桌面自动化兜底发送
│   ├── storage/                    # 存储层
│   │   └── db.py                   # 极简 SQLite 读写分离抽象
│   ├── tools/                      # 诊断与回放验证工具
│   │   └── replay_verifier.py      # 历史账单对账验证工具
│   ├── main.py                     # 单群模式启动入口
│   └── watchdog_slim.py            # 单进程看门狗守护
│
├── .gitignore
└── README.md
```

---

## 快速开始

### 1. 配置群路由
在 `redledger-multi/routes.json` 中配置群 ID 与对应端口：
```json
{
  "routes": {
    "59220588167@chatroom": {
      "name": "奥数群",
      "port": 8771,
      "db_path": "E:/RedLedgerData/groups/aoshu/database.db"
    },
    "default": {
      "name": "默认群",
      "port": 8770,
      "db_path": "E:/RedLedgerData/groups/default/database.db"
    }
  }
}
```

### 2. 启动集群
双击运行 `redledger-multi/start-cluster.bat`，即可一键拉起集群。

---

## 性能表现

在实际生产群及高频压力回放测试中：
- **网关路由分发延迟**：< 6ms
- **单局注单撮合与结算**：< 0.2ms
- **高清水印报表渲染**：< 80ms
- **整体端到端结算耗时**：< 95ms
- **并发跨群争用**：0 锁争用（完全进程隔离）
