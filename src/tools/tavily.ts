import { Type, type Static } from "@earendil-works/pi-ai";
import { defineTool, type ToolDefinition } from "@earendil-works/pi-coding-agent";

/**
 * TypeBox Schema for Tavily Web Search Input Parameters
 */
export const TavilySearchParameters = Type.Object(
  {
    query: Type.String({
      description: "The search query to look up on the web.",
    }),
    search_depth: Type.Optional(
      Type.Union([Type.Literal("basic"), Type.Literal("advanced")], {
        description:
          "Search depth. 'basic' is faster (recommended); 'advanced' conducts deeper crawling and higher relevance scoring.",
        default: "basic",
      })
    ),
    topic: Type.Optional(
      Type.Union([Type.Literal("general"), Type.Literal("news")], {
        description:
          "Category of search. 'general' for broad knowledge, 'news' for current events and recent articles.",
        default: "general",
      })
    ),
    max_results: Type.Optional(
      Type.Integer({
        description:
          "Maximum number of search results to return (between 1 and 10). Default is 5.",
        minimum: 1,
        maximum: 10,
        default: 5,
      })
    ),
    include_answer: Type.Optional(
      Type.Boolean({
        description:
          "Whether to include an AI-synthesized direct answer to the query based on search results. Default is true.",
        default: true,
      })
    ),
    include_raw_content: Type.Optional(
      Type.Boolean({
        description:
          "Whether to include the parsed raw text content of each result page. Default is false.",
        default: false,
      })
    ),
    include_images: Type.Optional(
      Type.Boolean({
        description:
          "Whether to include relevant images found in the search results. Default is false.",
        default: false,
      })
    ),
    include_domains: Type.Optional(
      Type.Array(Type.String(), {
        description:
          "A list of domains to exclusively include in the search (e.g. ['wikipedia.org', 'techcrunch.com']).",
      })
    ),
    exclude_domains: Type.Optional(
      Type.Array(Type.String(), {
        description:
          "A list of domains to exclude from search results.",
      })
    ),
    days: Type.Optional(
      Type.Integer({
        description:
          "When topic is 'news', the number of days back to search from today (e.g. 1 for last 24h, 7 for last week).",
        minimum: 1,
      })
    ),
  },
  {
    $id: "TavilySearchParameters",
    description: "Input parameters for conducting a web search via the Tavily Search API.",
  }
);

export type TavilySearchInput = Static<typeof TavilySearchParameters>;

/**
 * TypeBox Schema for an individual search result item
 */
export const TavilySearchResultItemSchema = Type.Object({
  title: Type.String({ description: "Title of the webpage" }),
  url: Type.String({ description: "URL of the webpage" }),
  content: Type.String({ description: "Snippet/summary of the content" }),
  score: Type.Number({ description: "Relevance score assigned by Tavily" }),
  raw_content: Type.Optional(Type.Union([Type.String(), Type.Null()])),
  published_date: Type.Optional(Type.String()),
});

export type TavilySearchResultItem = Static<typeof TavilySearchResultItemSchema>;

/**
 * TypeBox Schema for image result item
 */
export const TavilyImageResultItemSchema = Type.Object({
  url: Type.String({ description: "Direct URL of the image" }),
  description: Type.Optional(Type.String({ description: "Description or alt text of the image" })),
});

export type TavilyImageResultItem = Static<typeof TavilyImageResultItemSchema>;

/**
 * TypeBox Schema for Tavily Search Response
 */
export const TavilySearchResponseSchema = Type.Object({
  query: Type.String(),
  answer: Type.Optional(Type.String()),
  results: Type.Array(TavilySearchResultItemSchema),
  images: Type.Optional(Type.Array(TavilyImageResultItemSchema)),
  response_time: Type.Optional(Type.Number()),
});

export type TavilySearchResponse = Static<typeof TavilySearchResponseSchema>;

/**
 * Structured details for tool execution logs and UI rendering
 */
export interface TavilySearchToolDetails {
  query: string;
  resultCount: number;
  hasAnswer: boolean;
  responseTime?: number;
  rawResponse?: TavilySearchResponse;
}

/**
 * Configuration options for creating the Tavily Search Tool
 */
export interface TavilyToolOptions {
  /** Tavily API key. Defaults to process.env.TAVILY_API_KEY */
  apiKey?: string;
  /** Tavily API Base URL. Defaults to https://api.tavily.com */
  baseUrl?: string;
  /** Default search depth. Defaults to 'basic' */
  defaultSearchDepth?: "basic" | "advanced";
  /** Default max results to return. Defaults to 5 */
  defaultMaxResults?: number;
}

/**
 * Standalone programmatic caller for Tavily Search API
 */
export async function searchTavily(
  input: TavilySearchInput,
  options?: TavilyToolOptions & { signal?: AbortSignal }
): Promise<TavilySearchResponse> {
  const apiKey = options?.apiKey || process.env.TAVILY_API_KEY;
  if (!apiKey) {
    throw new Error(
      "Tavily API key is missing. Please set TAVILY_API_KEY in your environment or pass apiKey in options."
    );
  }

  const baseUrl = (options?.baseUrl || "https://api.tavily.com").replace(/\/+$/, "");
  const endpoint = `${baseUrl}/search`;

  const requestBody = {
    api_key: apiKey,
    query: input.query,
    search_depth: input.search_depth ?? options?.defaultSearchDepth ?? "basic",
    topic: input.topic ?? "general",
    max_results: input.max_results ?? options?.defaultMaxResults ?? 5,
    include_answer: input.include_answer ?? true,
    include_raw_content: input.include_raw_content ?? false,
    include_images: input.include_images ?? false,
    ...(input.include_domains?.length ? { include_domains: input.include_domains } : {}),
    ...(input.exclude_domains?.length ? { exclude_domains: input.exclude_domains } : {}),
    ...(input.days ? { days: input.days } : {}),
  };

  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify(requestBody),
    signal: options?.signal,
  });

  if (!response.ok) {
    let errorDetail = "";
    try {
      const errJson = await response.json();
      const raw = errJson.detail ?? errJson.message ?? errJson;
      errorDetail = typeof raw === "object" ? JSON.stringify(raw) : String(raw);
    } catch {
      errorDetail = await response.text();
    }
    throw new Error(`Tavily API error (${response.status} ${response.statusText}): ${errorDetail}`);
  }

  const data = (await response.json()) as TavilySearchResponse;
  return data;
}

/**
 * Formats Tavily search results into clean markdown for consumption by an LLM
 */
export function formatTavilyResultsForLlm(data: TavilySearchResponse): string {
  const sections: string[] = [];

  if (data.answer) {
    sections.push(`### Direct Answer:\n${data.answer}\n`);
  }

  if (data.results && data.results.length > 0) {
    sections.push(`### Web Search Results for "${data.query}":\n`);
    data.results.forEach((item, index) => {
      const date = item.published_date ? ` *(Published: ${item.published_date})*` : "";
      const snippet =
        item.content.length > 350 ? item.content.slice(0, 350) + "..." : item.content;
      sections.push(`${index + 1}. **[${item.title}](${item.url})**${date}\n   ${snippet}\n`);
    });
  } else {
    sections.push(`No web search results found for "${data.query}".`);
  }

  if (data.images && data.images.length > 0) {
    sections.push("### Relevant Images:");
    data.images.forEach((img) => {
      sections.push(`- ![${img.description || "Image"}](${img.url})`);
    });
  }

  return sections.join("\n").trim();
}

/**
 * Creates a ToolDefinition compatible with pi-coding-agent and harness configurations
 */
export function createTavilySearchTool(
  options?: TavilyToolOptions
): ToolDefinition<typeof TavilySearchParameters, TavilySearchToolDetails> {
  return defineTool({
    name: "web_search",
    label: "Web Search (Tavily)",
    description:
      "Search the live web using the Tavily Search API. Returns real-time search results, direct answers, and article snippets. Use this tool when you need up-to-date information, facts, news, documentation, or public profile details.",
    promptSnippet: "Search the web for up-to-date information, news, and facts via Tavily",
    promptGuidelines: [
      "Use web_search when the user asks about recent events, public people, documentation, or real-time web information.",
      "Formulate concise, specific search queries rather than conversational sentences.",
    ],
    parameters: TavilySearchParameters,
    outputSchema: TavilySearchResponseSchema,
    async execute(toolCallId, params, signal, onUpdate, _ctx) {
      try {
        if (onUpdate) {
          onUpdate({
            content: [{ type: "text", text: `Searching web for "${params.query}"...` }],
            details: {
              query: params.query,
              resultCount: 0,
              hasAnswer: false,
            },
          });
        }

        const data = await searchTavily(params, { ...options, signal });
        const formattedMarkdown = formatTavilyResultsForLlm(data);

        return {
          content: [{ type: "text", text: formattedMarkdown }],
          details: {
            query: params.query,
            resultCount: data.results?.length ?? 0,
            hasAnswer: Boolean(data.answer),
            responseTime: data.response_time,
            rawResponse: data,
          },
          structuredContent: data as any,
        };
      } catch (error: any) {
        return {
          isError: true,
          content: [
            {
              type: "text",
              text: `Error executing web search: ${error.message || String(error)}`,
            },
          ],
          details: {
            query: params.query,
            resultCount: 0,
            hasAnswer: false,
          },
        };
      }
    },
  });
}
