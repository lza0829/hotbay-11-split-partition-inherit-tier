import { useEffect, useState } from 'react'
import { api } from '../api'

export default function Splits() {
  const [parts, setParts] = useState([])
  const [srcId, setSrcId] = useState('')
  const [clips, setClips] = useState([])
  const [picked, setPicked] = useState([])
  const [newKey, setNewKey] = useState('')
  const [newTitle, setNewTitle] = useState('')
  const [receipts, setReceipts] = useState([])
  const [moves, setMoves] = useState([])
  const [msg, setMsg] = useState('')

  const reload = () => {
    api.partitions().then(setParts)
    api.splits().then(setReceipts)
  }
  useEffect(() => { reload() }, [])

  useEffect(() => {
    setPicked([])
    if (!srcId) { setClips([]); return }
    api.clips(srcId).then(setClips)
  }, [srcId])

  const src = parts.find(p => String(p.id) === String(srcId))
  const splittable = parts.filter(p => p.status === '在册')

  function toggle(id) {
    setPicked(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])
  }

  async function submit(e) {
    e.preventDefault()
    setMsg('')
    if (!srcId) { setMsg('请选择原分区'); return }
    if (picked.length === 0) { setMsg('禁止只开新号：至少改挂一段'); return }
    if (picked.length >= clips.length) {
      // 允许整册改挂，原册将关「已拆」，此处仅提示。
      setMsg('提示：已选中全部素材段，原分区将被关「已拆」')
    }
    try {
      const r = await api.createSplit({
        source_partition_id: Number(srcId),
        new_part_key: newKey.trim(),
        clip_ids: picked,
        title: newTitle.trim() || null,
      })
      setMsg(`拆号回执 ${r.split_id}：新分区 #${r.new_partition_id}（${r.new_tier}，继承原温层）· 改挂 ${r.moved_clip_count} 段 · 原分区 ${r.source_status_after}`)
      setPicked([]); setNewKey(''); setNewTitle('')
      reload()
      if (r.split_id) api.splitMoves(r.split_id).then(setMoves)
    } catch (err) { setMsg(err.message) }
  }

  return (
    <div className="page">
      <h1>拆号另立</h1>
      <p className="muted">新分区继承原分区现行温层与所属片库；被拆素材段改挂新分区，对象键沿用不双挂；原分区无残留段则关「已拆」。</p>
      <form className="panel form" onSubmit={submit}>
        <label>原分区<select value={srcId} onChange={e => setSrcId(e.target.value)}>
          <option value="">选择在册分区</option>
          {splittable.map(p => (
            <option key={p.id} value={p.id}>{p.part_key}（{p.tier} · {p.vault_name} · {p.clip_count}段）</option>
          ))}
        </select></label>
        {src && (
          <p className="muted">
            起始温层将继承：<strong>{src.tier}</strong> · 归属片库：<strong>{src.vault_name}</strong>
          </p>
        )}
        <label>新分区键<input value={newKey} onChange={e => setNewKey(e.target.value)} placeholder="如 P-红海2024-拆号" required /></label>
        <label>新片名（可空）<input value={newTitle} onChange={e => setNewTitle(e.target.value)} placeholder="默认原片名·拆号" /></label>
        <fieldset className="panel">
          <legend>选择改挂素材段（{picked.length}/{clips.length}）</legend>
          {clips.length === 0 && <p className="muted">原分区暂无素材段或未选择原分区。</p>}
          {clips.map(c => (
            <label key={c.id} className="check">
              <input type="checkbox" checked={picked.includes(c.id)} onChange={() => toggle(c.id)} />
              {c.object_key} · {c.note || '素材段'} · {c.bytes} 字节
            </label>
          ))}
        </fieldset>
        <button type="submit">执行拆号</button>
        {msg && <p className="msg">{msg}</p>}
      </form>

      <div className="table-wrap">
        <h2>拆号回执</h2>
        <table>
          <thead><tr><th>回执</th><th>原分区</th><th>新分区</th><th>原温层</th><th>新温层</th><th>改挂段</th><th>残留段</th><th>原册结果</th><th>时间</th></tr></thead>
          <tbody>
            {receipts.map(r => (
              <tr key={r.id}>
                <td>{r.id}</td><td>{r.source_part_key}</td><td>{r.new_part_key}</td>
                <td>{r.source_tier}</td><td>{r.new_tier}</td><td>{r.moved_clip_count}</td>
                <td>{r.remaining_clip_count}</td><td>{r.source_status_after}</td><td>{r.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {moves.length > 0 && (
        <div className="table-wrap">
          <h2>最近改挂段</h2>
          <table>
            <thead><tr><th>段号</th><th>对象键</th><th>原分区</th><th>新分区</th></tr></thead>
            <tbody>
              {moves.map(m => (
                <tr key={m.id}><td>{m.clip_id}</td><td>{m.object_key}</td><td>{m.from_partition_id}</td><td>{m.to_partition_id}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
