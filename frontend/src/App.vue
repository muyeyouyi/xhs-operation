<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
import NodeMetrics from "./components/NodeMetrics.vue";
import StreamOutput from "./components/StreamOutput.vue";
import {
  assetUrl,
  request,
  type Article,
  type History,
  type Task,
  type TaskSummary,
} from "./api";

const tasks = ref<TaskSummary[]>([]);
const current = ref<Task | null>(null);
const creating = ref(true);
const busy = ref(false);
const error = ref("");
const feedback = ref("");
const history = ref<History[] | null>(null);
const editing = ref(false);
const draft = reactive<Article>({ title: "", body: "" });
const form = reactive({
  direction: "",
  audience: "想学习 AI 应用开发的编程初学者",
  references: "",
  topic_count: 4,
  card_count: 4,
});
const waiting = computed(
  () => current.value?.status === "waiting" && !busy.value,
);
const labels: Record<string, string> = {
  queued: "排队中",
  running: "生成中",
  waiting: "待审核",
  failed: "执行失败",
  completed: "已完成",
  cancelled: "已取消",
};
const stages = ["topic", "article", "package", "completed"];
const stageIndex = computed(() =>
  stages.indexOf(current.value?.state.stage || "topic"),
);
let timer: ReturnType<typeof setInterval>;

async function perform(action: () => Promise<void>) {
  busy.value = true;
  error.value = "";
  try {
    await action();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}

async function refresh() {
  tasks.value = await request<TaskSummary[]>("/tasks");
  const id = current.value?.id;
  if (!id || creating.value) return;
  const task = await request<Task>(`/tasks/${id}`);
  if (current.value?.id !== id || creating.value) return;
  if (task.interrupt_id !== current.value.interrupt_id) {
    feedback.value = "";
    editing.value = false;
  }
  current.value = task;
}

function newTask() {
  creating.value = true;
  current.value = null;
  history.value = null;
  error.value = "";
}

async function openTask(id: string) {
  await perform(async () => {
    current.value = await request<Task>(`/tasks/${id}`);
    creating.value = false;
    history.value = null;
    feedback.value = "";
    editing.value = false;
  });
}

async function createTask() {
  await perform(async () => {
    current.value = await request<Task>("/tasks", form);
    creating.value = false;
    await refresh();
  });
}

async function decide(action: string, extra = {}) {
  const task = current.value!;
  await perform(async () => {
    await request(`/tasks/${task.id}/decisions`, {
      interrupt_id: task.interrupt_id,
      action,
      feedback: feedback.value,
      ...extra,
    });
    await refresh();
  });
}

function editArticle() {
  Object.assign(draft, current.value!.state.article);
  editing.value = true;
}

async function retry() {
  await perform(async () => {
    await request(`/tasks/${current.value!.id}/retry`, {});
    await refresh();
  });
}

async function showHistory() {
  await perform(async () => {
    history.value = history.value
      ? null
      : await request<History[]>(`/tasks/${current.value!.id}/history`);
  });
}

onMounted(async () => {
  await perform(refresh);
  timer = setInterval(() => {
    if (!busy.value)
      refresh().catch((e) => {
        error.value = e.message;
      });
  }, 2500);
});
onUnmounted(() => clearInterval(timer));
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-icon">知</span>
        <div>知页<span class="brand-sub">AI 内容工作台</span></div>
      </div>
      <button class="primary new-button" @click="newTask" :disabled="busy">
        ＋ 新建内容
      </button>
      <div class="section-label">
        内容任务 <span>{{ tasks.length }}</span>
      </div>
      <div v-if="!tasks.length" class="empty-nav">
        从一个内容方向开始，<br />积累你的技术内容库。
      </div>
      <button
        v-for="task in tasks"
        :key="task.id"
        class="task-item"
        :class="{ active: current?.id === task.id }"
        @click="openTask(task.id)"
        :disabled="busy"
      >
        <span class="task-name">{{ task.direction }}</span>
        <span class="task-meta"
          ><i :class="task.status"></i>{{ labels[task.status]
          }}<span>{{
            new Date(task.created_at).toLocaleDateString("zh-CN")
          }}</span></span
        >
      </button>
      <div class="sidebar-footer">教育内容 · 从想法到分享</div>
    </aside>

    <main>
      <header>
        <span>内容工作室 / {{ creating ? "新建内容" : "生产工作台" }}</span
        ><span class="local-tag">内部运营工具</span>
      </header>
      <div class="page">
        <div v-if="error" class="error" role="alert">
          {{ error
          }}<button @click="error = ''" aria-label="关闭错误提示">×</button>
        </div>
        <template v-if="creating">
          <div class="eyebrow">CREATE WITH INTENTION</div>
          <h1>好内容，从一个方向开始。</h1>
          <p class="intro">
            告诉 AI 你想讲什么。选定角度、打磨文章，再把知识变成值得收藏的图片。
          </p>
          <div class="steps">
            <span>01 输入方向</span><b>→</b><span>02 选择选题</span><b>→</b
            ><span>03 审核文章</span><b>→</b><span>04 图片与导出</span>
          </div>
          <form class="panel create-form" @submit.prevent="createTask">
            <label for="direction">这次想分享什么？ <small>必填</small></label>
            <textarea
              id="direction"
              v-model="form.direction"
              required
              minlength="2"
              maxlength="2000"
              rows="4"
              placeholder="例如：教零基础学员用 Python 和大模型 API，做一个能读取 PDF 的问答助手"
            ></textarea>
            <label for="audience">写给谁看</label>
            <input id="audience" v-model="form.audience" maxlength="1000" />
            <div class="form-row">
              <div>
                <label for="topics">候选选题</label
                ><select id="topics" v-model.number="form.topic_count">
                  <option v-for="n in [3, 4, 5]" :value="n">{{ n }} 个</option>
                </select>
              </div>
              <div>
                <label for="cards">干货图片</label
                ><select id="cards" v-model.number="form.card_count">
                  <option v-for="n in [3, 4, 5]" :value="n">{{ n }} 张</option>
                </select>
              </div>
            </div>
            <label for="references">参考素材 <small>选填</small></label>
            <textarea
              id="references"
              v-model="form.references"
              maxlength="20000"
              rows="3"
              placeholder="粘贴课程信息、技术资料或过往内容反馈，帮助 AI 更准确地选题。"
            ></textarea>
            <div class="form-footer">
              <span>AI 先推荐选题，选择后才生成文章。</span
              ><button
                class="primary"
                :disabled="busy || form.direction.trim().length < 2"
              >
                {{ busy ? "提交中…" : "生成选题 →" }}
              </button>
            </div>
          </form>
        </template>

        <template v-else-if="current">
          <NodeMetrics :task-id="current.id" />
          <div class="heading">
            <div>
              <div class="eyebrow">CONTENT WORKSPACE</div>
              <h1>{{ current.direction }}</h1>
            </div>
            <span class="badge">{{ labels[current.status] }}</span>
          </div>
          <div class="steps">
            <span
              v-for="(name, i) in [
                '选择选题',
                '审核文章',
                '审核成品',
                '导出素材',
              ]"
              :class="{ selected: stageIndex === i, done: stageIndex > i }"
              >0{{ i + 1 }} {{ name }}</span
            >
          </div>
          <div class="toolbar">
            <span>面向：{{ current.state.brief.audience }}</span
            ><button class="text-button" @click="showHistory" :disabled="busy">
              {{ history ? "收起历史" : "查看历史版本" }}
            </button>
          </div>
          <div
            v-if="current.status === 'queued' || current.status === 'running'"
            class="notice"
            role="status"
          >
            <span class="spinner"></span>AI
            正在处理，完成后会停在下一步等待你确认。
          </div>
          <div v-if="current.status === 'failed'" class="error">
            <div>
              <strong>本次生成未完成</strong>
              <p>{{ current.error }}</p>
            </div>
            <button @click="retry" :disabled="busy">重试当前步骤</button>
          </div>
          <div v-if="current.status === 'cancelled'" class="notice">
            此任务已取消。你可以新建任务继续创作。
          </div>

          <StreamOutput
            :key="current.id"
            :task-id="current.id"
            :status="current.status"
          />

          <section v-if="history" class="panel history">
            <h2>版本记录</h2>
            <details v-for="item in history" :key="item.checkpoint_id">
              <summary>
                {{ new Date(item.created_at).toLocaleString("zh-CN") }} ·
                {{ item.state.stage
                }}<span v-if="item.state.article_revision">
                  · 文章 V{{ item.state.article_revision }}</span
                >
              </summary>
              <h3>{{ item.state.article?.title }}</h3>
              <pre>{{
                item.state.article?.body ||
                JSON.stringify(item.state.topics, null, 2)
              }}</pre>
            </details>
          </section>

          <section v-if="current.state.stage === 'topic'" class="topic-grid">
            <article
              v-for="(topic, index) in current.state.topics"
              class="panel topic-card"
              :key="index"
            >
              <span class="eyebrow">选题 0{{ index + 1 }}</span>
              <h2>{{ topic.title }}</h2>
              <p>{{ topic.angle }}</p>
              <div class="reason">{{ topic.reason }}</div>
              <button
                @click="decide('select', { topic_index: index })"
                :disabled="!waiting"
              >
                选择这个选题 →
              </button>
            </article>
          </section>

          <section
            v-if="current.state.article && current.state.stage !== 'topic'"
            class="panel article"
          >
            <div class="panel-heading">
              <h2>
                公众号文章 <small>V{{ current.state.article_revision }}</small>
              </h2>
              <button
                v-if="current.state.stage === 'article'"
                class="text-button"
                @click="editArticle"
                :disabled="!waiting"
              >
                编辑文章
              </button>
            </div>
            <template v-if="editing"
              ><label for="article-title">标题</label
              ><input id="article-title" v-model="draft.title" /><label
                for="article-body"
                >正文（Markdown）</label
              ><textarea
                id="article-body"
                class="article-editor"
                v-model="draft.body"
                rows="20"
              ></textarea>
              <div class="actions">
                <button
                  class="primary"
                  @click="decide('edit', { article: draft })"
                  :disabled="
                    !waiting || !draft.title.trim() || !draft.body.trim()
                  "
                >
                  保存新版本并重新审核</button
                ><button @click="editing = false">放弃编辑</button>
              </div></template
            >
            <template v-else
              ><h3>{{ current.state.article.title }}</h3>
              <div class="article-body">
                {{ current.state.article.body }}
              </div></template
            >
          </section>

          <template
            v-if="current.state.package && current.state.assets?.length"
          >
            <h2 class="section-title">
              小红书图片 <small>{{ current.state.assets.length }} 张</small>
            </h2>
            <div class="image-grid">
              <a
                v-for="asset in current.state.assets"
                :href="assetUrl(asset)"
                target="_blank"
                rel="noopener"
                ><img :src="assetUrl(asset)" alt="技术干货卡片，点击查看原图"
              /></a>
            </div>
            <section class="panel">
              <h2>{{ current.state.package.title }}</h2>
              <div class="article-body">
                {{ current.state.package.caption }}
              </div>
            </section>
          </template>

          <div v-if="current.state.render_error" class="error" role="alert">
            {{ current.state.render_error }}
          </div>
          <section v-if="waiting && !editing" class="panel review">
            <h2>
              {{
                current.state.stage === "topic"
                  ? "想换一个角度？"
                  : "审核与修改"
              }}
            </h2>
            <label for="feedback">{{
              current.state.stage === "topic" ? "补充选题要求" : "修改意见"
            }}</label>
            <textarea
              id="feedback"
              v-model="feedback"
              rows="3"
              maxlength="4000"
              placeholder="例如：更适合初学者，增加一个完整的实践案例。"
            ></textarea>
            <div class="actions">
              <button
                v-if="current.state.stage !== 'topic'"
                class="primary"
                @click="decide('approve')"
                :disabled="busy || !!current.state.render_error"
              >
                {{
                  current.state.stage === "article"
                    ? "确认文章，生成图片 →"
                    : "确认成品，导出素材 →"
                }}
              </button>
              <button
                @click="
                  decide(
                    current.state.stage === 'topic' ? 'regenerate' : 'revise',
                  )
                "
                :disabled="
                  busy || (current.state.stage !== 'topic' && !feedback.trim())
                "
              >
                {{
                  current.state.stage === "topic"
                    ? "重新推荐选题"
                    : "按意见重新生成"
                }}
              </button>
              <button
                class="text-button danger"
                @click="decide('cancel')"
                :disabled="busy"
              >
                取消任务
              </button>
            </div>
          </section>
          <section
            v-if="current.status === 'completed' && current.state.bundle"
            class="panel complete"
          >
            <div>
              <div class="eyebrow">READY TO SHARE</div>
              <h2>内容已准备好，可以发布了。</h2>
              <p>素材包包含小红书图片与文案、公众号 Markdown 与 HTML。</p>
            </div>
            <a
              class="primary button-link"
              :href="assetUrl(current.state.bundle)"
              download
              >下载素材包 ↓</a
            >
          </section>
        </template>
      </div>
    </main>
  </div>
</template>
