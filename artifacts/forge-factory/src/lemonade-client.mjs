import { truncate } from './utils.mjs';

export class LemonadeClient {
  constructor(settings) {
    this.settings = settings;
    this.model = settings.model;
  }

  async health() {
    const response = await this.request('/models', { method: 'GET' });
    const data = await response.json();
    const models = data.data || data.models || [];
    if (this.model === 'AUTO-DETECT') this.model = models[0]?.id || models[0]?.name || null;
    return { ok: response.ok, model: this.model, models };
  }

  async chat({ messages, tools = [], temperature = 0.2, maxTokens = 4096 }) {
    const model = this.model === 'AUTO-DETECT' ? null : this.model;
    if (!model) await this.health();
    if (!this.model) throw new Error('Lemonade returned no available local models. Load a model before starting a Forge job.');
    const body = { model: this.model, messages, temperature, max_tokens: maxTokens };
    if (tools.length) body.tools = tools;
    const response = await this.request('/chat/completions', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body)
    });
    const raw = await response.text();
    if (!response.ok) throw new Error(`Lemonade chat completion failed (${response.status}): ${truncate(raw, 3000)}`);
    const data = JSON.parse(raw);
    const message = data.choices?.[0]?.message;
    if (!message) throw new Error(`Lemonade returned no assistant message: ${truncate(raw, 3000)}`);
    return { message, usage: data.usage || null, raw: data };
  }

  async request(route, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.settings.timeoutMs || 120000);
    try {
      return await fetch(`${this.settings.baseUrl}${route}`, {
        ...options,
        signal: controller.signal,
        headers: {
          authorization: `Bearer ${this.settings.apiKey || 'lemonade'}`,
          ...(options.headers || {})
        }
      });
    } finally {
      clearTimeout(timer);
    }
  }
}
