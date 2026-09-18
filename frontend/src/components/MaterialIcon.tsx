interface Props {
  name: string;
  className?: string;
  style?: React.CSSProperties;
}

export function MaterialIcon({ name, className = "", style }: Props) {
  return (
    <span className={`material-symbols-outlined ${className}`} style={style} aria-hidden="true">
      {name}
    </span>
  );
}
