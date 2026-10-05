using UnityEngine;

/// <summary>
/// 1回の攻撃で固定する照準。ShapeOriginは当たり判定の発生点、
/// ImpactPointは表示エフェクトへ渡す狙い位置、Directionは水平の攻撃方向。
/// </summary>
public readonly struct AttackAim
{
    public AttackAim(
        Vector3 shapeOrigin,
        Vector3 impactPoint,
        Vector3 direction)
    {
        ShapeOrigin = shapeOrigin;
        ImpactPoint = impactPoint;
        Direction = direction;
    }

    public Vector3 ShapeOrigin { get; }
    public Vector3 ImpactPoint { get; }
    public Vector3 Direction { get; }
}
