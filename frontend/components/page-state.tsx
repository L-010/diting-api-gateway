export function LoadingState({ label = "正在加载数据..." }: { label?: string }) { return <div className="state-box">{label}</div>; }
export function ErrorState({ message }: { message: string }) { return <div className="state-box error">{message}</div>; }
