<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { parse } from "partial-json";
import type { Topic } from "../api";

interface Draft {
  id: number;
  job_id: number;
  node: "propose_topics" | "write_article";
  status: string;
  output_text: string;
}
const props = defineProps<{ taskId: string; status: string }>();
const draft = ref<Draft | null>(null);
const disconnected = ref(false);
const active = computed(() => ["queued", "running"].includes(props.status));
let source: EventSource | null = null;

const content = computed(() => {
  try {
    return (parse(draft.value?.output_text || "{}") || {}) as {
      topics?: Partial<Topic>[];
      title?: string;
      body?: string;
    };
  } catch {
    // JSON 尚未形成可读字段时继续等待，不把半截 JSON 当作正式结果。
    return {};
  }
});
const topics = computed(() =>
  Array.isArray(content.value.topics) ? content.value.topics : [],
);
const failed = computed(
  () =>
    props.status === "failed" ||
    ["failed", "interrupted"].includes(draft.value?.status || ""),
);

watch(
  [() => props.taskId, active],
  () => {
    source?.close();
    source = null;
    disconnected.value = false;
    if (!active.value) return;
    draft.value = null;
    const connection = new EventSource(`/api/v1/tasks/${props.taskId}/stream`);
    source = connection;
    connection.onmessage = (event) => {
      if (source !== connection) return;
      // 每个事件是最新尝试的完整草稿快照，重连或重试均不会拼接旧内容。
      draft.value = JSON.parse(event.data);
      disconnected.value = false;
    };
    connection.onerror = () => {
      if (source === connection) disconnected.value = true;
    };
  },
  { immediate: true },
);
onUnmounted(() => source?.close());
</script>

<template>
  <section
    v-if="draft && (active || props.status === 'failed')"
    class="panel stream-output"
    aria-label="流式生成草稿"
  >
    <div class="stream-heading">
      <h2>
        {{ draft.node === "propose_topics" ? "选题正在生成" : "文章正在生成" }}
      </h2>
      <span class="stream-tag">流式草稿</span>
    </div>
    <p class="stream-note">文字会逐步显示，完整生成并校验后才能选择或审核。</p>
    <p v-if="disconnected" class="stream-message">
      连接暂时中断，正在重新连接；后台生成会继续。
    </p>
    <p v-else-if="failed" class="stream-message">
      本次生成失败，草稿不能用于下一步；重试时会重新展示。
    </p>
    <p v-else-if="draft.status === 'completed'" class="stream-note">
      生成完成，正在更新审核界面。
    </p>
    <template v-if="draft.node === 'propose_topics'">
      <div v-if="topics.length" class="stream-topics">
        <article v-for="(topic, index) in topics" :key="index">
          <small>选题草稿 {{ index + 1 }}</small>
          <h3>{{ topic.title || "正在组织标题…" }}</h3>
          <p v-if="topic.angle">{{ topic.angle }}</p>
          <p v-if="topic.reason" class="stream-reason">{{ topic.reason }}</p>
        </article>
      </div>
      <p v-else class="stream-placeholder">等待第一段选题内容…</p>
    </template>
    <template v-else
      ><h3 v-if="content.title">{{ content.title }}</h3>
      <div v-if="content.body" class="stream-body">
        {{ content.body
        }}<span
          v-if="draft.status === 'running'"
          class="stream-cursor"
          aria-hidden="true"
          >▍</span
        >
      </div>
      <p v-else class="stream-placeholder">
        等待第一段正文，模型思考内容不会展示…
      </p></template
    >
  </section>
</template>

<style scoped>
.stream-output {
  border-color: #b9cead;
  background: #fcfef9;
}
.stream-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.stream-heading h2 {
  margin: 0;
}
.stream-tag {
  border: 1px solid #c7d9bc;
  border-radius: 20px;
  padding: 5px 9px;
  color: #6d895c;
  font-size: 10px;
  white-space: nowrap;
}
.stream-note,
.stream-placeholder {
  color: #8a987d;
  font-size: 12px;
  line-height: 1.8;
}
.stream-message {
  color: #a06a42;
  font-size: 12px;
  line-height: 1.8;
}
.stream-placeholder {
  margin-top: 22px;
}
.stream-topics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 20px;
}
.stream-topics article {
  padding: 20px;
  background: #fff;
  border: 1px solid #e0e8d7;
  border-radius: 9px;
}
.stream-topics small {
  margin: 0;
  font-size: 10px;
  color: #8b9d7c;
}
.stream-topics h3 {
  margin: 12px 0;
  font-size: 17px;
}
.stream-topics p {
  font-size: 13px;
  line-height: 1.8;
  color: #6c7f5f;
}
.stream-reason {
  border-top: 1px solid #edf1e7;
  padding-top: 10px;
}
.stream-body {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 14px;
  line-height: 2;
  color: #51604a;
}
.stream-cursor {
  color: #6e9760;
  animation: blink 1s steps(2) infinite;
}
@keyframes blink {
  50% {
    opacity: 0;
  }
}
@media (max-width: 680px) {
  .stream-topics {
    grid-template-columns: 1fr;
  }
  .stream-heading h2 {
    font-size: 17px;
  }
}
</style>
