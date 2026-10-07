export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.status = status;
  }
}

export async function request(path, { body, csrf, signal } = {}) {
  let response;
  try {
    response = await fetch('/api/' + path, {
      credentials: 'same-origin',
      cache: 'no-store',
      signal,
      ...(body === undefined
        ? {}
        : {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRF-Token': csrf,
            },
            body: JSON.stringify(body),
          }),
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new ApiError('서버에 연결하지 못했습니다. 네트워크를 확인해 주세요.');
  }
  const data = await response.json().catch(() => {
    throw new ApiError(
      '서버 응답을 확인하지 못했습니다. 새로고침해 주세요.',
      response.status,
    );
  });
  if (!response.ok)
    throw new ApiError(
      data.error || '요청을 처리하지 못했습니다.',
      response.status,
    );
  return data;
}
