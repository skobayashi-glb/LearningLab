using System.Collections.Generic;
using UnityEngine;

[System.Serializable]
public struct EngagementRange
{
    public float circleRadius;
    public float sectorRadius;
    [Range(1f, 360f)] public float sectorAngle;

    public static EngagementRange Default => new EngagementRange
    {
        circleRadius = 1.1f,
        sectorRadius = GameConfig.cellSize * 1.25f,
        sectorAngle = 90f,
    };

    public EngagementRange Normalized()
    {
        EngagementRange fallback = Default;
        return new EngagementRange
        {
            circleRadius = circleRadius > 0f ? circleRadius : fallback.circleRadius,
            sectorRadius = sectorRadius > 0f ? sectorRadius : fallback.sectorRadius,
            sectorAngle = sectorAngle > 0f ? Mathf.Clamp(sectorAngle, 1f, 360f) : fallback.sectorAngle,
        };
    }

    public bool Contains(Vector3 origin, Vector3 forward, Vector3 target)
    {
        EngagementRange range = Normalized();
        Vector3 toTarget = target - origin;
        toTarget.y = 0f;
        float sqrDistance = toTarget.sqrMagnitude;

        if (sqrDistance <= range.circleRadius * range.circleRadius)
        {
            return true;
        }

        if (sqrDistance > range.sectorRadius * range.sectorRadius || sqrDistance <= 0.0001f)
        {
            return false;
        }

        forward.y = 0f;
        if (forward.sqrMagnitude <= 0.0001f)
        {
            forward = Vector3.forward;
        }

        float angle = Vector3.Angle(forward.normalized, toTarget.normalized);
        return angle <= range.sectorAngle * 0.5f;
    }

    /// <summary>対象の中心点ではなく、向き付きボディボックスとの重なりで接敵を判定する。</summary>
    public bool Overlaps(Vector3 origin, Vector3 forward, IDamageTarget target)
    {
        if (!CombatTargetReference.TryGetTransform(
            target,
            out Transform targetTransform)) return false;

        BodyBox bodyBox = target.BodyBox;
        Vector3 targetOrigin = targetTransform.position;
        Vector3 targetForward = targetTransform.forward;
        Vector3 closest = bodyBox.ClosestPoint(targetOrigin, targetForward, origin);
        if (Contains(origin, forward, closest)) return true;

        for (int cornerIndex = 0; cornerIndex < BodyBox.HorizontalCornerCount; cornerIndex++)
        {
            Vector3 corner = bodyBox.GetHorizontalCorner(
                targetOrigin,
                targetForward,
                cornerIndex);
            if (Contains(origin, forward, corner)) return true;
        }

        return false;
    }

    public float MaxRadius()
    {
        EngagementRange range = Normalized();
        return Mathf.Max(range.circleRadius, range.sectorRadius);
    }

    /// <summary>
    /// 拠点(占有セル群)がこの範囲に入っているか。拠点中心か占有セルのいずれかが範囲内なら true。
    /// ユニットの索敵で共有する判定。
    /// </summary>
    public bool ContainsBase(Vector3 origin, Vector3 forward, IReadOnlyList<Vector2Int> occupiedCells)
    {
        if (occupiedCells == null) return false;
        if (Contains(origin, forward, GridMath.BaseCenterWorld(occupiedCells))) return true;
        foreach (Vector2Int cell in occupiedCells)
        {
            if (Contains(origin, forward, GroundHeight.CellToWorld(cell))) return true;
        }
        return false;
    }
}
