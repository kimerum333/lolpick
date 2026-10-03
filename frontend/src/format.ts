export const number = (value: number) => new Intl.NumberFormat('ko-KR').format(value)
export const points = (value: number) => `${number(value)} P`
export const compact = (value: number) => value >= 10_000 ? `${Number((value / 10_000).toFixed(1))}만` : number(value)
export const dateTime = (value: number) => new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(value)
