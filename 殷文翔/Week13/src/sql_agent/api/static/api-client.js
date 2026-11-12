/** 同源 HTTP 客户端；所有响应在进入视图前验证结构。 */
export class APIError extends Error {
  constructor(message, { code = "network_error", status = null, details = [], requestId = null } = {}) {
    super(message);
    this.name = "APIError";
    Object.assign(this, { code, status, details, requestId });
  }
}

const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const isStringList = (value) => Array.isArray(value) && value.every((item) => typeof item === "string");
const isCount = (value) => Number.isSafeInteger(value) && value >= 0;

export function prepareQuestion(text) {
  const question = text.trim();
  const length = Array.from(question).length;
  return {
    question,
    length,
    error: length === 0 ? "请输入完整的查询问题。" : length > 2000 ? "问题不能超过 2000 个字符。" : "",
  };
}

function validOutput(value) {
  return isObject(value)
    && typeof value.question === "string" && typeof value.answer === "string"
    && (value.sql === null || typeof value.sql === "string")
    && isStringList(value.tables) && isStringList(value.evidence) && isStringList(value.caveats)
    && isCount(value.row_count) && isCount(value.repair_attempts)
    && typeof value.truncated === "boolean" && value.error === null;
}

function validError(value) {
  return isObject(value) && isObject(value.error)
    && typeof value.error.code === "string" && typeof value.error.message === "string"
    && Array.isArray(value.error.details)
    && value.error.details.every((item) => isObject(item)
      && typeof item.field === "string" && typeof item.message === "string");
}

async function requestJSON(url, options) {
  let response;
  try {
    response = await fetch(url, { cache: "no-store", ...options });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new APIError("无法连接服务或连接已中断，查询执行状态未知。请检查服务后重新提交完整问题。");
  }
  const requestId = response.headers.get("X-Request-ID");
  const protocolError = () => new APIError("服务返回格式异常，请检查后端服务。", {
    code: "invalid_response", status: response.status, requestId,
  });
  const mediaType = (response.headers.get("Content-Type") || "").split(";", 1)[0].trim().toLowerCase();
  if (mediaType !== "application/json" && !(mediaType.startsWith("application/") && mediaType.endsWith("+json"))) {
    throw protocolError();
  }
  let payload;
  try {
    payload = await response.json();
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw protocolError();
  }
  if (!response.ok) {
    if (!validError(payload)) throw protocolError();
    throw new APIError(payload.error.message, { ...payload.error, status: response.status, requestId });
  }
  return { payload, requestId, protocolError };
}

export async function query(question, signal) {
  const { payload, requestId, protocolError } = await requestJSON("/api/v1/queries", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }), signal,
  });
  if (!validOutput(payload)) throw protocolError();
  return { output: payload, requestId };
}

export async function checkReadiness(signal) {
  const { payload, protocolError } = await requestJSON("/readyz", { signal });
  if (!isObject(payload) || payload.status !== "ready") throw protocolError();
}
