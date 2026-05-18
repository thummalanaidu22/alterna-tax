import { type ReactNode } from "react";

interface CardProps {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
}

export function Card({ children, className = "", onClick }: CardProps) {
  return (
    <div
      onClick={onClick}
      className={`bg-gray-900 border border-gray-800 rounded-xl p-5 ${onClick ? "cursor-pointer hover:border-gray-700 transition-colors" : ""} ${className}`}
    >
      {children}
    </div>
  );
}
