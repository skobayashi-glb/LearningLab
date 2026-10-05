using System;
using System.Collections.Generic;

/// <summary>
/// 1体が抱えている状態と、その残り効果時間。
/// 仕様書§10.2の確定方針どおり、状態はスタックせず「付与されている/いない」の二値で、
/// 各状態は独立した残り時間を持つ。再付与は残り時間を初期値へ戻し、加算しない。
/// UnitVitality と同じく、MonoBehaviour から切り離したプレーンクラス。
/// </summary>
public sealed class UnitStatusState
{
    const float NoRemainingSeconds = 0f;
    const float MinimumDeltaSeconds = 0f;

    readonly Dictionary<StatusEffectId, float> remainingByStatus =
        new Dictionary<StatusEffectId, float>();
    readonly List<StatusEffectId> active = new List<StatusEffectId>();
    int batchDepth;
    bool changedDuringBatch;

    /// <summary>付与状態の増減が起きたときだけ発火する。見た目側が購読する。</summary>
    public event Action Changed;

    /// <summary>現在付与されている状態。付与された順で並ぶ。</summary>
    public IReadOnlyList<StatusEffectId> Active => active;

    public bool HasAny => active.Count > 0;

    public bool IsActive(StatusEffectId statusId)
    {
        return remainingByStatus.ContainsKey(statusId);
    }

    public float RemainingSeconds(StatusEffectId statusId)
    {
        return remainingByStatus.TryGetValue(statusId, out float remaining)
            ? remaining
            : NoRemainingSeconds;
    }

    /// <summary>反応判定用に、現在の状態集合を命中前スナップショットとして複製する。</summary>
    public HashSet<StatusEffectId> CreateSnapshot()
    {
        return new HashSet<StatusEffectId>(active);
    }

    /// <summary>複数状態の付与・延長・消費を1回のChanged通知へまとめる。</summary>
    public void BeginBatch()
    {
        batchDepth++;
    }

    public void EndBatch()
    {
        if (batchDepth <= 0) return;
        batchDepth--;
        if (batchDepth > 0 || !changedDuringBatch) return;
        changedDuringBatch = false;
        Changed?.Invoke();
    }

    /// <summary>
    /// 状態を付与する。既に付いている場合は残り時間を durationSeconds へ戻すだけで、
    /// 他の状態の残り時間には触れない。
    /// </summary>
    public void Apply(StatusEffectId statusId, float durationSeconds)
    {
        if (durationSeconds <= NoRemainingSeconds) return;

        bool isNew = !remainingByStatus.ContainsKey(statusId);
        remainingByStatus[statusId] = durationSeconds;
        if (isNew) active.Add(statusId);
        MarkChanged();
    }

    /// <summary>現在残っている時間へ加算する。未付与状態を新規付与はしない。</summary>
    public void Extend(StatusEffectId statusId, float additionalSeconds)
    {
        if (additionalSeconds <= NoRemainingSeconds
            || !remainingByStatus.TryGetValue(statusId, out float remaining))
        {
            return;
        }
        remainingByStatus[statusId] = remaining + additionalSeconds;
        MarkChanged();
    }

    /// <summary>残り時間を進め、切れた状態を取り除く。</summary>
    public void Tick(float deltaSeconds)
    {
        if (active.Count == 0) return;
        float step = Math.Max(MinimumDeltaSeconds, deltaSeconds);
        if (step <= MinimumDeltaSeconds) return;

        bool removedAny = false;
        for (int index = active.Count - 1; index >= 0; index--)
        {
            StatusEffectId statusId = active[index];
            float remaining = remainingByStatus[statusId] - step;
            if (remaining > NoRemainingSeconds)
            {
                remainingByStatus[statusId] = remaining;
                continue;
            }
            remainingByStatus.Remove(statusId);
            active.RemoveAt(index);
            removedAny = true;
        }
        if (removedAny) MarkChanged();
    }

    public void Remove(StatusEffectId statusId)
    {
        if (!remainingByStatus.Remove(statusId)) return;
        active.Remove(statusId);
        MarkChanged();
    }

    public void Clear()
    {
        if (active.Count == 0) return;
        remainingByStatus.Clear();
        active.Clear();
        MarkChanged();
    }

    void MarkChanged()
    {
        if (batchDepth > 0)
        {
            changedDuringBatch = true;
            return;
        }
        Changed?.Invoke();
    }
}
