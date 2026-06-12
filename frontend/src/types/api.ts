export interface Question {
  // Usually a number, but the LLM can emit string ids like "Q2" — never
  // Number() them (see Interview.tsx restore path).
  id: number | string;
  question: string;
  difficulty?: number;
  focus_area?: string;
  category?: string;
  intent?: string;
}

export interface Score {
  question_id: number;
  score: number;
  assessment?: string;
  improvement?: string;
  understanding?: string;
  weak_point?: string | null;
  key_missing?: string[];
  difficulty?: number;
}

export interface Overall {
  avg_score?: number;
  summary?: string;
  new_weak_points?: WeakPoint[];
  new_strong_points?: StrongPoint[];
  communication_observations?: CommunicationObservations;
  thinking_patterns?: ThinkingPatterns;
  topic_mastery?: { notes?: string; score?: number };
  dimension_scores?: Record<string, number>;
  role_fit_summary?: string;
}

export interface WeakPoint {
  point: string;
  topic?: string;
  times_seen?: number;
  improved?: boolean;
}

export interface StrongPoint {
  point: string;
  topic?: string;
}

export interface ThinkingPatterns {
  strengths?: string[];
  gaps?: string[];
}

export interface CommunicationObservations {
  style?: string;
  habits?: string[];
  suggestions?: string[];
  style_update?: string;
  new_habits?: string[];
  new_suggestions?: string[];
}

export interface ScoreHistoryEntry {
  date: string;
  avg_score: number;
  mode: string;
  topic?: string;
  dimension_scores?: Record<string, number>;
}

export interface TopicMastery {
  score?: number;
  level?: number;
  notes?: string;
  retrospective?: string;
  retrospective_at?: string;
  last_assessed?: string;
}

export interface Profile {
  stats?: {
    total_sessions: number;
    total_answers?: number;
    resume_sessions?: number;
    drill_sessions?: number;
    job_prep_sessions?: number;
    avg_score?: number | string;
    resume_avg_score?: number | string | null;
    drill_avg_score?: number | string | null;
    job_prep_avg_score?: number | string | null;
    score_history?: ScoreHistoryEntry[];
  };
  topic_mastery?: Record<string, TopicMastery>;
  previous_topic_mastery?: Record<string, TopicMastery>;
  weak_points?: WeakPoint[];
  strong_points?: StrongPoint[];
  thinking_patterns?: ThinkingPatterns;
  communication?: CommunicationObservations;
  updated_at?: string;
}

export interface Session {
  session_id: string;
  mode: string;
  topic?: string;
  created_at: string;
  questions?: Question[];
  scores?: Score[];
  review?: string;
  overall?: Overall;
  answers?: { question_id: number; answer: string }[];
}

export interface TopicInfo {
  name: string;
  icon?: string;
  dir?: string;
}

export interface AlgorithmCard {
  id: string;
  title: string;
  problem_text: string;
  difficulty: string;
  language: string;
  tags: string[];
  solution: string;
  conversation_history?: { role: string; content: string }[];
  source_url?: string;
  note?: string;
  created_at: string;
}

export interface Favorite {
  id: string;
  session_id?: string;
  question: string;
  user_answer?: string;
  reference_answer?: string;
  score?: number | null;
  assessment?: string;
  topic?: string;
  difficulty?: string;
  tags: string[];
  created_at: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface DueReview {
  point: string;
  topic?: string;
}

export interface InterviewStartResponse {
  session_id: string;
  mode: string;
  topic?: string;
  questions?: Question[];
  message?: string;
  preview?: Record<string, any>;
  company?: string;
  position?: string;
  meta?: Record<string, any>;
}

export interface EndInterviewResponse {
  session_id: string;
  mode: string;
  review: string;
  scores?: Score[];
  overall?: Overall;
  meta?: Record<string, any>;
  dimension_scores?: Record<string, number>;
  avg_score?: number;
}

export interface User {
  id: string;
  username: string;
  name?: string;
  email?: string;
}
