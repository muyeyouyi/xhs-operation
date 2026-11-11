<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { request, type NodeRun } from "../api";

const props = defineProps<{ taskId: string }>();
const nodes = ref<NodeRun[]>([]);
const now = ref(Date.now());
const error = ref(false);
let timer: ReturnType<typeof setInterval>;
let loadingTask: string | null = null;
const labels = {
  running: "执行中",
  completed: "已完成",
  waiting: "等待人工操作",
  failed: "执行失败",
  interrupted: "执行中断",
};

function duration(node: NodeRun) {
  return node.status === "running"
    ? Math.max(0, now.value - new Date(node.started_at).getTime())
    : node.duration_ms;
}
function formatTime(ms: number | null) {
  if (ms === null) return "未知";
  return ms < 1000 ? `${Math.round(ms)} 毫秒` : `${(ms / 1000).toFixed(2)} 秒`;
}
const totalTime = computed(() =>
  nodes.value.reduce((total, node) => total + (duration(node) ?? 0), 0),
);
const modelNodes = computed(() => nodes.value.filter((node) => node.model));
const unknownUsage = computed(() =>
  modelNodes.value.some(
    (node) => node.status !== "running" && node.total_tokens === null,
  ),
);
const totalTokens = computed(() => {
  if (
    modelNodes.value.length &&
    !modelNodes.value.some((node) => node.total_tokens !== null)
  )
    return "—";
  return nodes.value
    .reduce((total, node) => total + (node.total_tokens ?? 0), 0)
    .toLocaleString("zh-CN");
});
const completed = computed(
  () =>
    nodes.value.filter((node) =>
      ["completed", "failed", "interrupted"].includes(node.status),
    ).length,
);
const currentNode = computed(
  () =>
    nodes.value.find((node) => node.status === "running") || nodes.value.at(-1),
);
function tokens(node: NodeRun, value: number | null) {
  return value === null
    ? node.model
      ? node.status === "running"
        ? "统计中"
        : "未提供"
      : "—"
    : value.toLocaleString("zh-CN");
}

async function load() {
  const id = props.taskId;
  if (loadingTask === id) return;
  loadingTask = id;
  try {
    const data = await request<{ nodes: NodeRun[] }>(`/tasks/${id}/metrics`);
    if (props.taskId !== id) return;
    nodes.value = data.nodes;
    error.value = false;
  } catch {
    if (props.taskId === id) error.value = true;
  } finally {
    if (loadingTask === id) loadingTask = null;
  }
}
watch(
  () => props.taskId,
  () => {
    nodes.value = [];
    error.value = false;
    void load();
  },
  { immediate: true },
);
onMounted(() => {
  timer = setInterval(() => {
    now.value = Date.now();
    void load();
  }, 1000);
});
onUnmounted(() => clearInterval(timer));
</script>

<template>
  <section class="node-metrics" aria-label="节点执行指标">
    <div class="metrics-heading">
      <h2><span aria-hidden="true">▥</span> 节点执行指标</h2>
      <span class="metrics-refresh">每秒自动更新</span>
    </div>
    <div class="metrics-summary">
      <div>
        <strong>{{ formatTime(totalTime) }}</strong
        ><span>总耗时</span>
      </div>
      <div>
        <strong>{{ totalTokens }}</strong
        ><span>总 Token</span>
      </div>
      <div>
        <strong>{{ completed }}</strong
        ><span>已执行节点</span>
      </div>
    </div>
    <p class="metrics-current" role="status" aria-live="polite">
      <template v-if="currentNode"
        ><span :class="['run-dot', currentNode.status]"></span>当前：{{
          currentNode.subgraph
        }}
        · {{ currentNode.node_name
        }}<span class="current-status">{{
          labels[currentNode.status]
        }}</span></template
      >
      <template v-else>等待开始执行，节点启动后会显示在这里。</template>
    </p>
    <p v-if="error" class="metrics-error">暂时无法获取执行指标，正在重试。</p>
    <div v-if="nodes.length" class="node-runs">
      <article
        v-for="node in nodes.slice().reverse()"
        :key="node.id"
        :class="['node-run', node.status]"
      >
        <div class="run-title">
          <strong>{{ node.node_name }}</strong
          ><span class="run-state">{{ labels[node.status] }}</span
          ><small>{{ node.subgraph }} · 执行 {{ node.job_id }}</small>
        </div>
        <div class="run-data">
          <span
            >耗时 <b>{{ formatTime(duration(node)) }}</b></span
          ><span
            >输入 <b>{{ tokens(node, node.input_tokens) }}</b></span
          ><span
            >输出 <b>{{ tokens(node, node.output_tokens) }}</b></span
          ><span
            >总计 <b>{{ tokens(node, node.total_tokens) }}</b></span
          ><span class="run-model"
            >模型 <b>{{ node.model || "不调用模型" }}</b></span
          >
        </div>
      </article>
    </div>
    <p class="metrics-note">
      总耗时不含人工等待；重复执行分别记录，等待审核不计入已执行节点数。Token
      以模型实际返回为准。<span v-if="unknownUsage"
        >部分调用未提供用量，总计仅包含已知 Token。</span
      >
    </p>
  </section>
</template>

<style scoped>
.node-metrics {
  margin-bottom: 34px;
  padding-bottom: 26px;
  border-bottom: 1px solid #dfe6d9;
}
.metrics-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}
.metrics-heading h2 {
  margin: 0;
  font-size: 19px;
}
.metrics-heading h2 span {
  color: #497a64;
  margin-right: 6px;
}
.metrics-refresh,
.metrics-note {
  color: #8b9884;
  font-size: 11px;
}
.metrics-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.metrics-summary > div {
  padding: 24px 12px;
  border: 1px solid #e1e7db;
  border-radius: 10px;
  background: #fff;
  text-align: center;
}
.metrics-summary strong {
  display: block;
  color: #2b7458;
  font-size: 30px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
}
.metrics-summary > div > span {
  display: block;
  color: #86947d;
  font-size: 12px;
  margin-top: 12px;
}
.metrics-current {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  color: #526b48;
  font-size: 12px;
  margin: 20px 0 14px;
  line-height: 1.8;
}
.current-status {
  margin-left: auto;
}
.run-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #9baf8b;
}
.run-dot.running {
  background: #2a8a62;
  animation: pulse 1s ease-in-out infinite;
}
.run-dot.failed,
.run-dot.interrupted {
  background: #b96a50;
}
.node-runs {
  display: grid;
  gap: 10px;
  max-height: 340px;
  overflow-y: auto;
}
.node-run {
  padding: 16px 18px;
  border: 1px solid #e1e7db;
  border-radius: 9px;
  background: #fff;
}
.node-run.running {
  border-color: #9ebd9e;
  background: #f4f9ef;
}
.node-run.failed {
  border-color: #e8c2b3;
}
.run-title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 9px;
  margin-bottom: 12px;
}
.run-title strong {
  font-size: 13px;
}
.run-title small {
  margin: 0 0 0 auto;
  font-size: 10px;
}
.run-state {
  font-size: 10px;
  color: #748968;
}
.running .run-state {
  color: #277453;
}
.failed .run-state {
  color: #a35639;
}
.run-data {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
  font-size: 11px;
  color: #96a08c;
  line-height: 1.8;
}
.run-data b {
  color: #506647;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.run-model {
  overflow-wrap: anywhere;
}
.metrics-note {
  line-height: 1.8;
  margin: 13px 0 0;
}
.metrics-error {
  color: #a66b45;
  font-size: 12px;
}
@keyframes pulse {
  50% {
    opacity: 0.35;
  }
}
@media (max-width: 680px) {
  .metrics-summary {
    gap: 8px;
  }
  .metrics-summary strong {
    font-size: 20px;
  }
  .metrics-summary > div {
    padding: 18px 5px;
  }
  .metrics-refresh {
    font-size: 10px;
  }
  .run-data {
    gap: 7px 12px;
  }
  .run-title small {
    width: 100%;
    margin-left: 0;
  }
  .current-status {
    margin-left: 0;
  }
  .node-run {
    padding: 14px;
  }
}
</style>
