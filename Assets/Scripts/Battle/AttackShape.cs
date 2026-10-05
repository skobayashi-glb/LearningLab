using System.Collections.Generic;
using UnityEngine;

public enum AttackShapeType
{
    Circle,
    Single,
    Cone,
    Line,
}

/// <summary>
/// キャラごとの攻撃の水平当たり判定形状。
/// Circleは射程内の着弾点、Cone/Lineは攻撃者のボディ前端を起点とする。
/// </summary>
[System.Serializable]
public struct AttackShape
{
    const float MinimumShapeDimension = 0f;
    const float MaximumConeAngleDegrees = 360f;
    const float HalfRatio = 0.5f;
    const float MinimumFallbackExtent = 0.1f;
    const float FullCircleDegrees = 360f;
    const float MinimumGeometrySqrMagnitude = 0.000001f;
    const float MaximumSegmentRatio = 1f;

    public AttackShapeType type;

    [Tooltip("円AoEの当たり半径。0以下なら攻撃者のAttackRangeの半分を使用する")]
    public float areaRadius;
    [Tooltip("直線範囲の長さ（ワールド単位）")]
    public float lineLength;
    [Tooltip("直線範囲の幅（ワールド単位）")]
    public float lineWidth;
    [Tooltip("扇範囲の距離（ワールド単位）")]
    public float coneDistance;
    [Tooltip("扇範囲の角度（度）")]
    public float coneAngleDegrees;

    public static AttackShape Default => new AttackShape
    {
        type = AttackShapeType.Circle,
        areaRadius = 0f,
        lineLength = 0f,
        lineWidth = 0f,
        coneDistance = 0f,
        coneAngleDegrees = 0f,
    };

    public AttackShape Normalized()
    {
        return new AttackShape
        {
            type = type,
            areaRadius = Mathf.Max(MinimumShapeDimension, areaRadius),
            lineLength = Mathf.Max(MinimumShapeDimension, lineLength),
            lineWidth = Mathf.Max(MinimumShapeDimension, lineWidth),
            coneDistance = Mathf.Max(MinimumShapeDimension, coneDistance),
            coneAngleDegrees = Mathf.Clamp(
                coneAngleDegrees,
                MinimumShapeDimension,
                MaximumConeAngleDegrees),
        };
    }

    public float ResolveExtent(float attackRange)
    {
        switch (type)
        {
            case AttackShapeType.Single:
                return MinimumShapeDimension;
            case AttackShapeType.Cone:
                return coneDistance > MinimumShapeDimension
                    ? coneDistance
                    : Mathf.Max(MinimumFallbackExtent, attackRange);
            case AttackShapeType.Line:
                return lineLength > MinimumShapeDimension
                    ? lineLength
                    : Mathf.Max(MinimumFallbackExtent, attackRange);
            case AttackShapeType.Circle:
            default:
                return areaRadius > MinimumShapeDimension
                    ? areaRadius
                    : Mathf.Max(MinimumFallbackExtent, attackRange * HalfRatio);
        }
    }

    /// <summary>攻撃者と候補対象から、この形状用の照準を作る。</summary>
    public AttackAim CreateAim(
        Transform attacker,
        BodyBox attackerBody,
        IDamageTarget target,
        float attackRange)
    {
        if (attacker == null
            || !CombatTargetReference.TryGetTransform(
                target,
                out Transform targetTransform))
        {
            return default;
        }

        Vector3 targetBodyCenter = CombatGeometry.ClosestPoint(target, attacker.position);
        return CreateAim(
            attacker,
            attackerBody,
            targetBodyCenter,
            attackRange);
    }

    /// <summary>拠点などTransformを戦闘対象として公開しない対象点への照準を作る。</summary>
    public AttackAim CreateAim(
        Transform attacker,
        BodyBox attackerBody,
        Vector3 targetPoint,
        float attackRange)
    {
        if (attacker == null) return default;

        Vector3 attackerBodyCenter = attackerBody.WorldCenter(
            attacker.position,
            attacker.forward);
        Vector3 direction = ResolveDirection(
            attackerBodyCenter,
            targetPoint,
            attacker.forward);

        if (type == AttackShapeType.Circle
            || type == AttackShapeType.Single)
        {
            Vector3 impactPoint = ClampToReach(
                attacker.position,
                targetPoint,
                attackRange);
            return new AttackAim(impactPoint, impactPoint, direction);
        }

        float bodySupport = attackerBody.SupportDistance(
            attacker.forward,
            direction);
        Vector3 shapeOrigin = attackerBodyCenter + direction * bodySupport;
        return new AttackAim(shapeOrigin, targetPoint, direction);
    }

    /// <summary>固定済みの照準形状が対象の水平ボディボックスと重なるか。</summary>
    public bool Overlaps(
        AttackAim aim,
        IDamageTarget target,
        float attackRange)
    {
        if (!CombatTargetReference.TryGetTransform(
                target,
                out Transform targetTransform)) return false;

        if (target is ICombatFootprintTarget footprint)
            return OverlapsOccupiedCells(aim, footprint.OccupiedCells, attackRange);
        return OverlapsBody(
            aim,
            target.BodyBox,
            targetTransform.position,
            targetTransform.forward,
            attackRange);
    }

    /// <summary>攻撃形状が拠点のいずれかの占有セルと交差するか。</summary>
    public bool OverlapsOccupiedCells(
        AttackAim aim,
        IReadOnlyList<Vector2Int> occupiedCells,
        float attackRange)
    {
        if (type == AttackShapeType.Single
            || occupiedCells == null)
        {
            return false;
        }

        BodyBox cellBody = CreateOccupiedCellBody();
        for (int cellIndex = 0;
            cellIndex < occupiedCells.Count;
            cellIndex++)
        {
            Vector3 cellPosition = GridMath.CellToWorld(
                occupiedCells[cellIndex]);
            if (OverlapsBody(
                    aim,
                    cellBody,
                    cellPosition,
                    Vector3.forward,
                    attackRange))
            {
                return true;
            }
        }
        return false;
    }

    bool OverlapsBody(
        AttackAim aim,
        BodyBox targetBody,
        Vector3 targetPosition,
        Vector3 targetForward,
        float attackRange)
    {
        switch (type)
        {
            case AttackShapeType.Single:
                return false;
            case AttackShapeType.Cone:
                return OverlapsCone(
                    aim,
                    targetBody,
                    targetPosition,
                    targetForward,
                    ResolveExtent(attackRange),
                    coneAngleDegrees);
            case AttackShapeType.Line:
                return OverlapsLine(
                    aim,
                    targetBody,
                    targetPosition,
                    targetForward,
                    ResolveExtent(attackRange),
                    Mathf.Max(MinimumShapeDimension, lineWidth));
            case AttackShapeType.Circle:
            default:
                float radius = ResolveExtent(attackRange);
                Vector3 closest = targetBody.ClosestPoint(
                    targetPosition,
                    targetForward,
                    aim.ImpactPoint);
                Vector3 d = closest - aim.ImpactPoint;
                d.y = 0f;
                return d.sqrMagnitude <= radius * radius;
        }
    }

    static bool OverlapsCone(
        AttackAim aim,
        BodyBox targetBody,
        Vector3 targetPosition,
        Vector3 targetForward,
        float distance,
        float angleDegrees)
    {
        float normalizedAngle = Mathf.Clamp(
            angleDegrees,
            MinimumShapeDimension,
            FullCircleDegrees);
        Vector3 closest = targetBody.ClosestPoint(
            targetPosition,
            targetForward,
            aim.ShapeOrigin);
        if (ContainsConePoint(
                aim.ShapeOrigin,
                aim.Direction,
                closest,
                distance,
                normalizedAngle))
        {
            return true;
        }

        for (int cornerIndex = 0;
            cornerIndex < BodyBox.HorizontalCornerCount;
            cornerIndex++)
        {
            Vector3 corner = targetBody.GetHorizontalCorner(
                targetPosition,
                targetForward,
                cornerIndex);
            if (ContainsConePoint(
                    aim.ShapeOrigin,
                    aim.Direction,
                    corner,
                    distance,
                    normalizedAngle))
            {
                return true;
            }
        }

        Vector3 coneEnd = aim.ShapeOrigin + aim.Direction * distance;
        return targetBody.ContainsPoint(
            targetPosition,
            targetForward,
            coneEnd);
    }

    static bool ContainsConePoint(
        Vector3 origin,
        Vector3 direction,
        Vector3 point,
        float distance,
        float angleDegrees)
    {
        Vector3 delta = point - origin;
        delta.y = 0f;
        float sqrDistance = delta.sqrMagnitude;
        if (sqrDistance <= MinimumGeometrySqrMagnitude) return true;
        if (sqrDistance > distance * distance) return false;
        if (angleDegrees >= FullCircleDegrees) return true;
        return Vector3.Angle(direction, delta) <= angleDegrees * HalfRatio;
    }

    static bool OverlapsLine(
        AttackAim aim,
        BodyBox targetBody,
        Vector3 targetPosition,
        Vector3 targetForward,
        float length,
        float width)
    {
        Vector3 segmentStart3 = aim.ShapeOrigin;
        Vector3 segmentEnd3 = aim.ShapeOrigin + aim.Direction * length;
        if (targetBody.ContainsPoint(
                targetPosition,
                targetForward,
                segmentStart3)
            || targetBody.ContainsPoint(
                targetPosition,
                targetForward,
                segmentEnd3))
        {
            return true;
        }

        Vector2 segmentStart = ToHorizontal(segmentStart3);
        Vector2 segmentEnd = ToHorizontal(segmentEnd3);
        float halfWidth = width * HalfRatio;
        float halfWidthSqr = halfWidth * halfWidth;
        for (int cornerIndex = 0;
            cornerIndex < BodyBox.HorizontalCornerCount;
            cornerIndex++)
        {
            int nextCornerIndex = (cornerIndex + 1)
                % BodyBox.HorizontalCornerCount;
            Vector2 corner = ToHorizontal(targetBody.GetHorizontalCorner(
                targetPosition,
                targetForward,
                cornerIndex));
            Vector2 nextCorner = ToHorizontal(targetBody.GetHorizontalCorner(
                targetPosition,
                targetForward,
                nextCornerIndex));

            if (SegmentsIntersect(segmentStart, segmentEnd, corner, nextCorner)
                || PointSegmentSqrDistance(corner, segmentStart, segmentEnd)
                    <= halfWidthSqr
                || PointSegmentSqrDistance(segmentStart, corner, nextCorner)
                    <= halfWidthSqr
                || PointSegmentSqrDistance(segmentEnd, corner, nextCorner)
                    <= halfWidthSqr)
            {
                return true;
            }
        }
        return false;
    }

    static BodyBox CreateOccupiedCellBody()
    {
        return new BodyBox
        {
            size = new Vector3(
                GameConfig.cellSize,
                MinimumShapeDimension,
                GameConfig.cellSize),
            offset = Vector3.zero,
        };
    }

    static bool SegmentsIntersect(
        Vector2 a,
        Vector2 b,
        Vector2 c,
        Vector2 d)
    {
        Vector2 ab = b - a;
        Vector2 cd = d - c;
        float denominator = Cross(ab, cd);
        Vector2 ac = c - a;
        if (Mathf.Abs(denominator) <= MinimumGeometrySqrMagnitude)
        {
            return PointSegmentSqrDistance(a, c, d)
                    <= MinimumGeometrySqrMagnitude
                || PointSegmentSqrDistance(b, c, d)
                    <= MinimumGeometrySqrMagnitude
                || PointSegmentSqrDistance(c, a, b)
                    <= MinimumGeometrySqrMagnitude;
        }

        float first = Cross(ac, cd) / denominator;
        float second = Cross(ac, ab) / denominator;
        return first >= MinimumShapeDimension
            && first <= MaximumSegmentRatio
            && second >= MinimumShapeDimension
            && second <= MaximumSegmentRatio;
    }

    static float PointSegmentSqrDistance(
        Vector2 point,
        Vector2 segmentStart,
        Vector2 segmentEnd)
    {
        Vector2 segment = segmentEnd - segmentStart;
        float sqrLength = segment.sqrMagnitude;
        if (sqrLength <= MinimumGeometrySqrMagnitude)
            return (point - segmentStart).sqrMagnitude;
        float t = Mathf.Clamp01(
            Vector2.Dot(point - segmentStart, segment) / sqrLength);
        return (point - (segmentStart + segment * t)).sqrMagnitude;
    }

    static Vector3 ResolveDirection(
        Vector3 origin,
        Vector3 target,
        Vector3 fallback)
    {
        Vector3 direction = target - origin;
        direction.y = 0f;
        if (direction.sqrMagnitude <= MinimumGeometrySqrMagnitude)
        {
            direction = fallback;
            direction.y = 0f;
        }
        return direction.sqrMagnitude > MinimumGeometrySqrMagnitude
            ? direction.normalized
            : Vector3.forward;
    }

    static Vector3 ClampToReach(Vector3 from, Vector3 to, float reach)
    {
        Vector3 delta = to - from;
        delta.y = 0f;
        float distance = delta.magnitude;
        Vector3 flatTarget = new Vector3(to.x, from.y, to.z);
        if (distance <= reach || distance <= MinimumGeometrySqrMagnitude)
            return flatTarget;
        return from + delta / distance * reach;
    }

    static Vector2 ToHorizontal(Vector3 point)
    {
        return new Vector2(point.x, point.z);
    }

    static float Cross(Vector2 left, Vector2 right)
    {
        return left.x * right.y - left.y * right.x;
    }
}
