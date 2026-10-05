using UnityEngine;

/// <summary>
/// ユニット基準点からの中心オフセットと、ローカル軸上の外形寸法。
/// 戦闘判定では高さを保持しつつ、水平面の向き付き矩形として扱う。
/// </summary>
[System.Serializable]
public struct BodyBox
{
    const float MinimumDimension = 0f;
    const float DefaultVerticalCenterRatio = 0.5f;
    const float MinimumDirectionSqrMagnitude = 0.000001f;
    const float MinimumAxisProjection = 0.000001f;
    const float NoSurfaceDistance = 0f;
    const float HalfExtentRatio = 0.5f;
    public const int HorizontalCornerCount = 4;

    public Vector3 size;
    public Vector3 offset;

    public static BodyBox Default => new BodyBox
    {
        size = Vector3.one * GameConfig.cellSize,
        offset = new Vector3(
            0f,
            GameConfig.cellSize * DefaultVerticalCenterRatio,
            0f),
    };

    public BodyBox Normalized()
    {
        return new BodyBox
        {
            size = new Vector3(
                Mathf.Max(MinimumDimension, size.x),
                Mathf.Max(MinimumDimension, size.y),
                Mathf.Max(MinimumDimension, size.z)),
            offset = offset,
        };
    }

    /// <summary>ユニット基準点と向きから、オフセット込みのワールド中心を求める。</summary>
    public Vector3 WorldCenter(Vector3 origin, Vector3 forward)
    {
        ResolveHorizontalAxes(forward, out Vector3 right, out Vector3 normalizedForward);
        return origin
            + right * offset.x
            + Vector3.up * offset.y
            + normalizedForward * offset.z;
    }

    /// <summary>水平面で、このボックス内の worldPoint に最も近い点を返す。</summary>
    public Vector3 ClosestPoint(Vector3 origin, Vector3 forward, Vector3 worldPoint)
    {
        BodyBox box = Normalized();
        ResolveHorizontalAxes(forward, out Vector3 right, out Vector3 normalizedForward);
        Vector3 center = box.WorldCenter(origin, normalizedForward);
        Vector3 delta = worldPoint - center;
        float halfWidth = box.size.x * 0.5f;
        float halfDepth = box.size.z * 0.5f;
        float localX = Mathf.Clamp(Vector3.Dot(delta, right), -halfWidth, halfWidth);
        float localZ = Mathf.Clamp(Vector3.Dot(delta, normalizedForward), -halfDepth, halfDepth);
        return center + right * localX + normalizedForward * localZ;
    }

    /// <summary>水平面で worldPoint がこのボックス内にあるか。</summary>
    public bool ContainsPoint(Vector3 origin, Vector3 forward, Vector3 worldPoint)
    {
        Vector3 closest = ClosestPoint(origin, forward, worldPoint);
        Vector3 delta = worldPoint - closest;
        delta.y = 0f;
        return delta.sqrMagnitude <= MinimumDirectionSqrMagnitude;
    }

    /// <summary>水平断面を必ず包む円の半径。</summary>
    public float HorizontalCircumradius()
    {
        BodyBox box = Normalized();
        Vector2 halfExtents = new Vector2(box.size.x, box.size.z) * 0.5f;
        return halfExtents.magnitude;
    }

    /// <summary>
    /// origin から worldDirection 方向へ進んだとき、水平断面の表面へ抜けるまでの距離。
    /// SupportDistance が返す「向きへの投影半幅」と違い、実際に体が届く距離を返す。
    /// 細長い体を斜め方向から測ると両者は大きく食い違うので、
    /// 間合い・食い込み判定にはこちらを使う。
    /// </summary>
    public float SurfaceDistanceAlong(Vector3 origin, Vector3 forward, Vector3 worldDirection)
    {
        BodyBox box = Normalized();
        worldDirection.y = 0f;
        if (worldDirection.sqrMagnitude <= MinimumDirectionSqrMagnitude) return NoSurfaceDistance;
        worldDirection.Normalize();
        ResolveHorizontalAxes(forward, out Vector3 right, out Vector3 normalizedForward);
        Vector3 toOrigin = origin - box.WorldCenter(origin, normalizedForward);
        float exit = Mathf.Min(
            ResolveSlabExit(
                Vector3.Dot(toOrigin, right),
                Vector3.Dot(worldDirection, right),
                box.size.x * HalfExtentRatio),
            ResolveSlabExit(
                Vector3.Dot(toOrigin, normalizedForward),
                Vector3.Dot(worldDirection, normalizedForward),
                box.size.z * HalfExtentRatio));
        return float.IsPositiveInfinity(exit)
            ? NoSurfaceDistance
            : Mathf.Max(NoSurfaceDistance, exit);
    }

    /// <summary>1軸のスラブを抜けるまでの距離。平行なら内側は無限、外側は交差なし。</summary>
    static float ResolveSlabExit(float origin, float direction, float halfExtent)
    {
        if (Mathf.Abs(direction) <= MinimumAxisProjection)
        {
            return Mathf.Abs(origin) <= halfExtent
                ? float.PositiveInfinity
                : NoSurfaceDistance;
        }
        return ((direction > NoSurfaceDistance ? halfExtent : -halfExtent) - origin) / direction;
    }

    /// <summary>worldDirection 方向へ、中心から水平断面が張り出す距離。</summary>
    public float SupportDistance(Vector3 forward, Vector3 worldDirection)
    {
        BodyBox box = Normalized();
        ResolveHorizontalAxes(forward, out Vector3 right, out Vector3 normalizedForward);
        worldDirection.y = 0f;
        if (worldDirection.sqrMagnitude <= MinimumDirectionSqrMagnitude) return 0f;

        worldDirection.Normalize();
        float halfWidth = box.size.x * 0.5f;
        float halfDepth = box.size.z * 0.5f;
        return halfWidth * Mathf.Abs(Vector3.Dot(right, worldDirection))
            + halfDepth * Mathf.Abs(Vector3.Dot(normalizedForward, worldDirection));
    }

    /// <summary>水平断面の4頂点を、右前・左前・左後・右後の順で書き込む。</summary>
    public void GetHorizontalCorners(Vector3 origin, Vector3 forward, Vector3[] corners)
    {
        if (corners == null || corners.Length < HorizontalCornerCount)
        {
            throw new System.ArgumentException(
                $"BodyBoxの頂点出力先には{HorizontalCornerCount}要素以上が必要です。",
                nameof(corners));
        }

        BodyBox box = Normalized();
        ResolveHorizontalAxes(forward, out Vector3 right, out Vector3 normalizedForward);
        Vector3 center = box.WorldCenter(origin, normalizedForward);
        Vector3 rightExtent = right * (box.size.x * 0.5f);
        Vector3 forwardExtent = normalizedForward * (box.size.z * 0.5f);
        corners[0] = center + rightExtent + forwardExtent;
        corners[1] = center - rightExtent + forwardExtent;
        corners[2] = center - rightExtent - forwardExtent;
        corners[3] = center + rightExtent - forwardExtent;
    }

    public Vector3 GetHorizontalCorner(Vector3 origin, Vector3 forward, int cornerIndex)
    {
        if (cornerIndex < 0 || cornerIndex >= HorizontalCornerCount)
        {
            throw new System.ArgumentOutOfRangeException(nameof(cornerIndex));
        }

        BodyBox box = Normalized();
        ResolveHorizontalAxes(forward, out Vector3 right, out Vector3 normalizedForward);
        Vector3 center = box.WorldCenter(origin, normalizedForward);
        float rightSign = cornerIndex == 0 || cornerIndex == 3 ? 1f : -1f;
        float forwardSign = cornerIndex <= 1 ? 1f : -1f;
        return center
            + right * (box.size.x * 0.5f * rightSign)
            + normalizedForward * (box.size.z * 0.5f * forwardSign);
    }

    static void ResolveHorizontalAxes(Vector3 forward, out Vector3 right, out Vector3 normalizedForward)
    {
        normalizedForward = forward;
        normalizedForward.y = 0f;
        if (normalizedForward.sqrMagnitude <= MinimumDirectionSqrMagnitude)
        {
            normalizedForward = Vector3.forward;
        }
        else
        {
            normalizedForward.Normalize();
        }

        right = Vector3.Cross(Vector3.up, normalizedForward);
    }
}
