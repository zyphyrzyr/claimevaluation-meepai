/** 简单行级 diff（LCS）：标注 v1 删除行 / v2 新增行 */

export interface DiffRow {
  type: 'same' | 'add' | 'remove'
  text: string
}

export function diffLines(a: string, b: string): DiffRow[] {
  const la = a.split('\n')
  const lb = b.split('\n')
  const n = la.length
  const m = lb.length
  // LCS DP 表（备忘录行数有限，可承受）
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = la[i] === lb[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const rows: DiffRow[] = []
  let i = 0, j = 0
  while (i < n && j < m) {
    if (la[i] === lb[j]) {
      rows.push({ type: 'same', text: la[i] })
      i++; j++
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      rows.push({ type: 'remove', text: la[i] })
      i++
    } else {
      rows.push({ type: 'add', text: lb[j] })
      j++
    }
  }
  while (i < n) rows.push({ type: 'remove', text: la[i++] })
  while (j < m) rows.push({ type: 'add', text: lb[j++] })
  return rows
}
