# Transformer 架构深度解析

## 1. 为什么需要 Transformer

### RNN/LSTM 的局限
- **序列依赖**：必须按时间步顺序处理，无法并行化，训练速度慢
- **长距离依赖问题**：即使 LSTM 通过门控机制缓解了梯度消失，仍然难以捕获超长距离的依赖关系（超过几百步就开始退化）
- **信息瓶颈**：Encoder-Decoder 架构中，Encoder 将整个句子压缩为一个固定长度的向量（context vector），信息损失严重

### Attention 的出现
- Bahdanau Attention（2014）：允许 Decoder 在每个时间步"回头看"Encoder 的所有隐藏状态，而不是只依赖最后一个
- Luong Attention（2015）：简化了对齐函数，提出 dot-product、general、concat 三种计算方式
- 但仍然保留了 RNN 结构，计算仍然是串行的

### Transformer 的突破（2017）
- Google 论文 "Attention Is All You Need"（Vaswani et al.）
- **完全抛弃 RNN 和 CNN**，仅用注意力机制
- 核心优势：
  1. **并行计算**：所有位置同时计算注意力，训练效率大幅提升
  2. **全局依赖**：任意两个位置之间的距离都是 O(1)
  3. **可扩展性**：可以通过堆叠更多层、更大的模型来持续提升性能（Scaling Law）

---

## 2. Self-Attention 自注意力机制

### 核心思想
对于输入序列中的每个 token，计算它与序列中所有其他 token 的相关性得分，然后用这些得分对所有 token 的表示进行加权求和，得到每个 token 的新表示。

### 数学推导

#### Step 1：生成 Q, K, V
对于输入 X（shape: [n, d_model]），通过三个不同的线性变换得到：
- **Query（Q）**= X * W_Q，shape: [n, d_k]，代表"我想查询什么信息"
- **Key（K）**= X * W_K，shape: [n, d_k]，代表"我包含什么信息"
- **Value（V）**= X * W_V，shape: [n, d_v]，代表"如果你需要我，这是我能提供的信息"

其中 W_Q, W_K 的 shape 为 [d_model, d_k]，W_V 的 shape 为 [d_model, d_v]。

#### Step 2：计算注意力分数

$$\text{Attention}(Q, K, V) = \text{softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right) V$$

详细分解：
1. **计算相似度**：`S = Q * K^T`，shape: [n, n]
   - S[i][j] 表示第 i 个 token 的 query 与第 j 个 token 的 key 的点积
   - 点积越大，说明两个 token 越相关

2. **缩放（Scaling）**：`S = S / sqrt(d_k)`
   - 为什么要除以 sqrt(d_k)？
   - 假设 Q 和 K 的各维度独立同分布，均值为 0，方差为 1
   - 则 q_i * k_i 的均值为 0，方差为 1
   - d_k 维的点积 = sum(q_i * k_i)，方差为 d_k
   - 当 d_k 很大时（如 64），点积值会很大，softmax 会进入饱和区（梯度接近 0）
   - 除以 sqrt(d_k) 使方差回到 1，保持梯度稳定

3. **归一化**：`A = softmax(S)`
   - 对每一行做 softmax，使权重之和为 1
   - A[i][j] 表示第 i 个 token 对第 j 个 token 的注意力权重

4. **加权求和**：`Output = A * V`，shape: [n, d_v]
   - 每个 token 的输出是所有 Value 的加权组合

#### 完整计算示例
```
假设序列长度 n=3, d_k=d_v=4：

输入 X = [[x1], [x2], [x3]]   # [3, d_model]

Q = X * W_Q  # [3, 4]
K = X * W_K  # [3, 4]
V = X * W_V  # [3, 4]

# 计算注意力矩阵
S = Q * K^T / sqrt(4)  # [3, 3]

      k1    k2    k3
q1 [  2.1,  0.5, -0.3 ]    # token1 对各 token 的相关性
q2 [  0.3,  1.8,  0.7 ]    # token2 对各 token 的相关性
q3 [ -0.1,  0.6,  2.5 ]    # token3 对各 token 的相关性

A = softmax(S)  # 每行归一化
      k1    k2    k3
q1 [ 0.72, 0.15, 0.13 ]    # token1 主要关注自己
q2 [ 0.12, 0.55, 0.33 ]    # token2 主要关注自己和 token3
q3 [ 0.05, 0.10, 0.85 ]    # token3 主要关注自己

Output = A * V  # [3, 4]，每个 token 的新表示
```

### 直觉理解
想象你在图书馆找书：
- Query = 你要找的主题（"Python 并发编程"）
- Key = 每本书的标签（"Python 基础"、"并发编程"、"Java 入门"...）
- Value = 每本书的实际内容
- Attention Score = 你的主题和每本书标签的匹配程度
- 最终输出 = 按匹配程度加权混合所有书的内容

### Masked Self-Attention（因果注意力）
- Decoder 中使用，防止看到未来的 token
- 在注意力分数矩阵上施加上三角掩码：将未来位置设为 -inf
- softmax 后未来位置的权重变为 0
```
Mask:
[  0,  -inf, -inf ]
[  0,    0,  -inf ]
[  0,    0,    0  ]
```

### Cross-Attention（交叉注意力）
- Encoder-Decoder 架构中使用
- Q 来自 Decoder 层，K 和 V 来自 Encoder 的输出
- 允许 Decoder 在生成每个 token 时"查阅"Encoder 的完整输出

---

## 3. Multi-Head Attention 多头注意力

### 为什么需要多头
单个注意力头只能关注一种模式的关系。多头注意力让模型可以同时关注不同类型的信息：
- 有的头关注语法关系（主谓关系）
- 有的头关注语义关系（同义词、上下位词）
- 有的头关注位置关系（相邻词）
- 有的头关注指代关系（代词和被指代的实体）

### 计算过程

$$\text{MultiHead}(Q, K, V) = \text{Concat}(\text{head}_1, \ldots, \text{head}_h) \cdot W_O$$

$$\text{head}_i = \text{Attention}(Q W_Q^i, K W_K^i, V W_V^i)$$

- h 个注意力头各自独立计算
- 每个头的维度 d_k = d_model / h（如 d_model=768, h=12, 则 d_k=64）
- 最后拼接所有头的输出（shape: [n, h * d_k] = [n, d_model]），再做一次线性变换 W_O

### 参数量分析
```
单头 Attention 参数：
  W_Q: d_model * d_model = d^2
  W_K: d_model * d_model = d^2
  W_V: d_model * d_model = d^2
  总计: 3d^2

Multi-Head Attention 参数：
  h 个头的 W_Q: h * (d_model * d_k) = h * d * (d/h) = d^2
  h 个头的 W_K: d^2
  h 个头的 W_V: d^2
  W_O: d_model * d_model = d^2
  总计: 4d^2

结论: MHA 参数量与单头几乎相同，但表达能力更强
```

### 计算复杂度
- Self-Attention 时间复杂度：O(n^2 * d)，n 是序列长度，d 是维度
- 空间复杂度：O(n^2)（注意力矩阵）
- 这是 Transformer 处理超长文本的瓶颈

---

## 4. 位置编码 Positional Encoding

### 为什么需要位置编码
Self-Attention 是置换不变的（permutation invariant）：打乱输入顺序不会改变输出。但语言是有序的，"猫吃鱼"和"鱼吃猫"含义不同。位置编码为模型注入序列顺序信息。

### Sinusoidal（正弦余弦）位置编码

$$PE_{(pos, 2i)} = \sin\left(\frac{pos}{10000^{2i/d_{model}}}\right)$$
$$PE_{(pos, 2i+1)} = \cos\left(\frac{pos}{10000^{2i/d_{model}}}\right)$$

- 每个位置生成一个唯一的 d_model 维向量
- 与输入嵌入相加：`input = token_embedding + positional_encoding`
- **优点**：
  - 理论上可以外推到训练时未见过的位置
  - 相对位置信息可以通过线性变换获取：PE(pos+k) 可以表示为 PE(pos) 的线性函数
- **缺点**：
  - 不可学习，表达能力有限
  - 实际中外推能力有限

### Learned（可学习）位置编码
- 为每个位置维护一个可学习的向量，训练过程中优化
- BERT、GPT-2 使用
- **优点**：更灵活，表达能力强
- **缺点**：不能外推到训练时未见过的长度

### RoPE（Rotary Position Embedding，旋转位置编码）
- 当前最主流的位置编码方式，LLaMA、Qwen、Mistral、DeepSeek 等都在使用
- 核心思想：将位置信息编码为旋转矩阵，直接注入到 Q 和 K 中

**数学原理**：
- 将 Q 和 K 向量的每两个维度视为复数平面上的一个点
- 根据位置 pos，将这个点旋转角度 theta_pos
- 旋转角度：theta_i = pos / 10000^(2i/d)

```
对于位置 m 处的 query 向量 q，应用 RoPE：
q_rope[2i]   = q[2i] * cos(m * theta_i) - q[2i+1] * sin(m * theta_i)
q_rope[2i+1] = q[2i] * sin(m * theta_i) + q[2i+1] * cos(m * theta_i)
```

**关键性质**：
- 两个位置 m 和 n 的注意力分数只取决于它们的相对距离 |m-n|
- 天然具备相对位置感知能力
- 计算高效，无需额外参数

**长度外推技术**：
- **NTK-aware Scaling**：调整旋转基频，使模型适应更长的序列
- **YaRN（Yet another RoPE extensioN）**：结合 NTK 和注意力缩放，外推效果更好
- **Dynamic NTK**：根据输入长度动态调整基频
- 实践中可以将 4K 训练长度的模型扩展到 32K-128K

### ALiBi（Attention with Linear Biases）
- 不在嵌入层加位置编码，而是直接在注意力分数上加线性偏置
- 偏置值 = -m * |i - j|，m 是每个头特有的斜率参数
- 距离越远，惩罚越大，注意力越小
- **优点**：
  - 天然支持长度外推，无需任何修改
  - 实现简单，不增加参数
- **缺点**：
  - 假设距离越远注意力越小，对某些任务不够灵活
- BLOOM、MPT 等模型使用

### 位置编码方式对比

| 方式 | 类型 | 外推能力 | 代表模型 |
|------|------|---------|---------|
| Sinusoidal | 绝对 | 一般 | 原始 Transformer |
| Learned | 绝对 | 差 | BERT, GPT-2 |
| RoPE | 相对 | 好（需外推技术） | LLaMA, Qwen, Mistral |
| ALiBi | 相对 | 天然外推 | BLOOM, MPT |

---

## 5. 完整 Transformer 结构

### Encoder 层（每层包含）
1. Multi-Head Self-Attention
2. Add & Norm（残差连接 + 层归一化）
3. Feed-Forward Network（FFN）：两层全连接，中间用 ReLU/GELU 激活
4. Add & Norm

### Decoder 层（每层包含）
1. **Masked** Multi-Head Self-Attention（掩码防止看到未来 token）
2. Add & Norm
3. Multi-Head **Cross**-Attention（Q 来自 Decoder，K/V 来自 Encoder 输出）
4. Add & Norm
5. Feed-Forward Network
6. Add & Norm

### Pre-Norm vs Post-Norm
- **Post-Norm**（原始论文）：`output = LayerNorm(x + SubLayer(x))`
  - 先计算子层，再加残差和 LayerNorm
  - 训练不稳定，需要 warmup
- **Pre-Norm**（现代常用）：`output = x + SubLayer(LayerNorm(x))`
  - 先 LayerNorm，再计算子层，再加残差
  - 训练更稳定，收敛更快
- 大多数现代 LLM 使用 Pre-Norm + RMSNorm

### RMSNorm（Root Mean Square Layer Normalization）
```
LayerNorm: y = (x - mean) / sqrt(var + eps) * gamma + beta
RMSNorm:   y = x / sqrt(mean(x^2) + eps) * gamma
```
- 去掉了均值中心化和偏置项
- 计算更简单，速度快约 10-15%
- 效果与 LayerNorm 相当
- LLaMA、Qwen 等使用

### FFN 变体
- **原始 FFN**：
  ```
  FFN(x) = W_2 * ReLU(W_1 * x + b_1) + b_2
  ```
  中间维度通常是 d_model 的 4 倍（如 d_model=4096，FFN 中间维度=16384）

- **SwiGLU**（LLaMA、Qwen 使用）：
  ```
  FFN(x) = (W_1 * x .* SiLU(W_3 * x)) * W_2
  ```
  - SiLU(x) = x * sigmoid(x)，也叫 Swish
  - 引入门控机制（Gated Linear Unit），效果显著优于 ReLU
  - 代价是多了一个 W_3 矩阵，为保持参数量不变通常将中间维度缩小为 8/3 * d_model

- **GeGLU**：类似 SwiGLU 但用 GELU 激活

---

## 6. KV Cache 原理与优化

### KV Cache 基本原理
- 自回归生成时，每生成一个新 token 都需要计算整个序列的 attention
- 但之前 token 的 K 和 V 不会改变（因为是 causal attention，之前的 token 看不到后面的）
- KV Cache：缓存已计算过的 K 和 V，新 token 只需计算自己的 Q、K、V

```
不使用 KV Cache（生成第 t 个 token 时）：
  Q = X_all * W_Q    # 重新计算所有 token 的 Q [t, d]
  K = X_all * W_K    # 重新计算所有 token 的 K [t, d]
  V = X_all * W_V    # 重新计算所有 token 的 V [t, d]
  Attn = softmax(Q * K^T / sqrt(d)) * V

使用 KV Cache（生成第 t 个 token 时）：
  q_new = x_t * W_Q        # 只计算新 token 的 Q [1, d]
  k_new = x_t * W_K        # 只计算新 token 的 K [1, d]
  v_new = x_t * W_V        # 只计算新 token 的 V [1, d]
  K_cache = concat(K_cache, k_new)  # 拼接到缓存 [t, d]
  V_cache = concat(V_cache, v_new)  # [t, d]
  Attn = softmax(q_new * K_cache^T / sqrt(d)) * V_cache  # [1, d]
```

- 计算量从 O(t^2 * d) 降到 O(t * d)，每步只处理一个新 token

### KV Cache 显存计算
```
KV Cache 大小 = 2 * n_layers * n_heads * d_head * seq_len * batch_size * dtype_bytes

示例（LLaMA-2 7B, 序列长度 4096, batch_size=1, FP16）：
  2 * 32层 * 32头 * 128维 * 4096 * 1 * 2字节
  = 2 * 32 * 32 * 128 * 4096 * 2
  ≈ 2 GB

如果 batch_size=8：约 16 GB！
```

### MHA / MQA / GQA 对比

#### MHA（Multi-Head Attention，标准多头注意力）
- 每个注意力头都有独立的 Q、K、V 投影
- KV Cache 最大
- 效果最好但显存开销大

#### MQA（Multi-Query Attention，多查询注意力）
- 所有 Q 头共享同一组 K 和 V（只有 1 个 KV 头）
- KV Cache 缩小 h 倍（h 为 Q 的头数）
- 推理速度快，但效果有一定下降
- GPT-J、PaLM 使用

#### GQA（Grouped-Query Attention，分组查询注意力）
- 介于 MHA 和 MQA 之间
- 将 Q 头分为 g 组，每组共享一个 KV 头
- KV Cache 缩小 h/g 倍
- 在效果和效率之间取得良好平衡

```
假设 32 个 Q 头：
  MHA:  32 个 KV 头  →  KV Cache = 32x
  GQA:   8 个 KV 头  →  KV Cache = 8x  (分 8 组)
  MQA:   1 个 KV 头  →  KV Cache = 1x
```

| 方案 | KV 头数 | 推理速度 | 模型效果 | 代表模型 |
|------|---------|---------|---------|---------|
| MHA | h | 慢 | 最好 | GPT-3, BERT |
| GQA | g (1<g<h) | 快 | 接近 MHA | LLaMA-2 70B, Mistral, Qwen-2 |
| MQA | 1 | 最快 | 稍差 | PaLM, GPT-J |

---

## 7. FlashAttention 原理

### 问题背景
- 标准 Attention 需要将完整的 n*n 注意力矩阵存储在 GPU 显存（HBM）中
- GPU 有两级存储：HBM（大但慢，如 40GB A100）和 SRAM（小但快，约 20MB）
- 标准实现频繁在 HBM 和 SRAM 之间搬运数据，成为计算瓶颈

### FlashAttention 核心思想
- **IO-aware 算法**：减少 HBM 和 SRAM 之间的数据搬运次数
- **分块计算（Tiling）**：将 Q, K, V 按块加载到 SRAM，在 SRAM 内完成计算
- **在线 softmax（Online Softmax）**：不需要一次性看到整行就能计算 softmax
  - 利用分块计算的 max 和 sum，在线更新 softmax 分母

### 关键技术细节
```
标准 Attention:
1. 计算 S = Q * K^T          → 写入 HBM (O(n^2) 读写)
2. 计算 P = softmax(S)       → 读 S 写 P 到 HBM (O(n^2) 读写)
3. 计算 O = P * V             → 读 P 和 V (O(n^2) 读写)
总 HBM 读写: O(n^2 * d + n^2) → IO 瓶颈

FlashAttention:
1. 将 Q, K, V 按块 (Block_size * d) 加载到 SRAM
2. 在 SRAM 内计算注意力（S, P, O 都在 SRAM 内）
3. 累积结果写回 HBM
总 HBM 读写: O(n^2 * d^2 / M)，M 是 SRAM 大小 → 大幅减少
```

### 效果
- 速度快 2-4 倍
- 显存从 O(n^2) 降到 O(n)（不需要存储完整的 n*n 注意力矩阵）
- FlashAttention-2 进一步优化了并行策略，速度再提升约 2 倍
- FlashAttention-3 针对 Hopper 架构（H100）优化
- 已成为现代 LLM 训练和推理的标配

---

## 8. 三种架构范式

### Encoder-Only（如 BERT）
- 输入整个序列，输出每个 token 的**双向上下文**表示
- 每个 token 可以看到前后所有 token
- 适合：文本分类、NER、句子相似度、抽取式问答
- 代表模型：BERT、RoBERTa、ALBERT、DeBERTa、ELECTRA
- 预训练任务：MLM（Masked Language Modeling）+ NSP

### Decoder-Only（如 GPT）
- 自回归生成，每个 token 只能看到当前位置之前的 token
- 使用 Causal Mask 实现
- 适合：文本生成、对话、代码生成、推理
- 代表模型：GPT 系列、LLaMA、Qwen、DeepSeek、Claude、Gemini
- **当前大模型的绝对主流架构**
- 预训练任务：CLM（Causal Language Modeling，预测下一个 token）

### Encoder-Decoder（如 T5）
- Encoder 处理输入（双向注意力），Decoder 生成输出（因果注意力+交叉注意力）
- 适合：翻译、摘要、结构化输出
- 代表模型：T5、BART、mBART、Flan-T5
- 预训练任务：Span Corruption（T5）、去噪目标（BART）

### 为什么 Decoder-Only 成为主流

| 原因 | 说明 |
|------|------|
| Scaling Law | Decoder-Only 在 scale up 时收益最大 |
| 统一性 | 所有任务都可以统一为"预测下一个 token" |
| 涌现能力 | 大规模 Decoder-Only 模型展现出涌现能力 |
| 训练效率 | CLM 天然可以利用序列中所有位置的 loss |
| In-context Learning | Decoder-Only 天然支持 few-shot 学习 |

---

## 9. 主流架构对比

### GPT 系列
- 架构：Decoder-Only
- GPT-1（117M）→ GPT-2（1.5B）→ GPT-3（175B）→ GPT-4（MoE, 未公开参数量）
- 位置编码：Learned（GPT-2/3）
- 激活函数：GELU
- 特点：开创了大模型 + Prompt 的范式

### BERT
- 架构：Encoder-Only，12/24 层
- 参数量：110M（Base）/ 340M（Large）
- 预训练：MLM（随机 mask 15% 的 token）+ NSP
- 位置编码：Learned（最大 512）
- 特点：双向上下文理解，NLU 任务经典模型

### T5（Text-to-Text Transfer Transformer）
- 架构：Encoder-Decoder
- 将所有 NLP 任务统一为 text-to-text 格式
- 如分类任务：输入 "classify: This is great" → 输出 "positive"
- 参数量：60M 到 11B

### LLaMA 系列（Meta）
- 架构：Decoder-Only
- LLaMA-1/2/3 逐代演进
- 核心技术：RoPE + SwiGLU + RMSNorm + Pre-Norm + GQA（2 70B 起）
- LLaMA-3：8B/70B/405B，128K 上下文
- 特点：开源社区生态最丰富，大量微调模型基于 LLaMA

### Qwen 系列（阿里）
- 架构：Decoder-Only
- Qwen-2.5：0.5B 到 72B 多种规格
- 核心技术：RoPE + SwiGLU + RMSNorm + GQA
- 特点：中文能力突出，工具调用和 Agent 能力强，支持 128K 上下文

### DeepSeek 系列
- DeepSeek-V2/V3：MoE 架构
- DeepSeek-V3：671B 总参数，每 token 激活 37B
- 核心创新：
  - **MLA（Multi-head Latent Attention）**：压缩 KV Cache 到低维潜在空间，比 GQA 更高效
  - **DeepSeekMoE**：细粒度专家 + 共享专家
  - **FP8 混合精度训练**
- DeepSeek-R1：推理增强模型，使用 GRPO 对齐

### Mistral / Mixtral
- Mistral 7B：Sliding Window Attention + GQA
- Mixtral 8x7B：MoE 架构，8 个专家每次激活 2 个
- 特点：小模型也能有很强的性能

### 架构技术总结

| 模型 | Norm | 位置编码 | FFN | 注意力 | 特殊技术 |
|------|------|---------|-----|--------|---------|
| GPT-3 | LayerNorm | Learned | ReLU | MHA | - |
| BERT | LayerNorm | Learned | GELU | MHA | MLM+NSP |
| LLaMA-2 | RMSNorm | RoPE | SwiGLU | GQA (70B) | - |
| Qwen-2.5 | RMSNorm | RoPE | SwiGLU | GQA | YaRN 外推 |
| DeepSeek-V3 | RMSNorm | RoPE | SwiGLU | MLA | MoE+FP8 |
| Mistral | RMSNorm | RoPE | SwiGLU | GQA | SWA |

---

## 10. Sparse Attention 稀疏注意力

### 为什么需要稀疏注意力
- 标准 Self-Attention 复杂度 O(n^2)，当序列长度 n=100K 时计算量巨大
- 实际上大多数 token 并不需要关注序列中所有其他 token

### 主要方法

#### Sliding Window Attention（滑动窗口注意力）
- 每个 token 只关注前后 w 个 token（局部注意力）
- 复杂度从 O(n^2) 降到 O(n * w)
- Mistral 使用，w=4096
- 通过多层叠加，高层 token 可以间接"看到"更远的位置

#### Longformer
- 组合局部注意力 + 全局注意力
- 大部分 token 使用局部注意力（窗口大小 w）
- 少数特殊 token（如 [CLS]）使用全局注意力

#### BigBird
- 随机注意力 + 局部注意力 + 全局注意力
- 理论上证明这种稀疏模式可以近似完整注意力

---

## 面试高频问题

### Q1: Self-Attention 的完整计算过程？为什么要除以 sqrt(d_k)？
**答**：Self-Attention 通过线性变换得到 Q, K, V，计算 Q*K^T 得到注意力分数，除以 sqrt(d_k) 缩放后做 softmax 归一化，再与 V 相乘得到加权输出。除以 sqrt(d_k) 是因为当 d_k 较大时，点积的方差为 d_k，值会很大导致 softmax 梯度消失。缩放使方差回到 1，保持梯度稳定。

### Q2: Multi-Head Attention 的作用？为什么不用单个大的注意力头？
**答**：MHA 让模型同时在不同子空间中关注不同类型的关系（语法、语义、位置等）。虽然参数量与单头相当，但多头提供了更丰富的表示能力。类比 CNN 中多个 filter 提取不同特征。

### Q3: RoPE 位置编码的原理和优势？
**答**：RoPE 将位置信息编码为旋转变换，对 Q 和 K 向量的每两个维度进行基于位置的旋转。核心性质是两个位置的注意力分数只取决于相对距离，天然支持相对位置编码。通过 NTK-aware、YaRN 等技术可以实现长度外推。

### Q4: KV Cache 的原理？GQA 如何优化？
**答**：KV Cache 缓存已生成 token 的 K 和 V，避免每步重复计算。GQA 让多个 Q 头共享 KV 头，将 KV Cache 大小从 h 份缩减为 g 份（g 为 KV 头组数），在效果和效率间取得平衡。

### Q5: FlashAttention 为什么能加速？
**答**：FlashAttention 通过分块计算和在线 softmax，减少 GPU HBM 和 SRAM 之间的数据搬运。标准实现需要将完整 n*n 注意力矩阵写入 HBM，FlashAttention 在 SRAM 中完成分块计算，显存从 O(n^2) 降到 O(n)，速度快 2-4 倍。

### Q6: Encoder-Only、Decoder-Only、Encoder-Decoder 的区别？为什么现在主流是 Decoder-Only？
**答**：三者分别对应双向理解（BERT）、自回归生成（GPT）、条件生成（T5）。Decoder-Only 成为主流因为：Scaling Law 收益最大、所有任务可统一为 next-token prediction、涌现能力、训练效率高、天然支持 in-context learning。

### Q7: Pre-Norm 和 Post-Norm 的区别？
**答**：Post-Norm 在残差连接之后做归一化，训练不稳定需要 warmup。Pre-Norm 在子层之前做归一化，训练更稳定。现代 LLM 普遍使用 Pre-Norm + RMSNorm（比 LayerNorm 更高效）。

### Q8: LLaMA 和 GPT 架构的主要区别？
**答**：LLaMA 使用 RoPE（vs Learned PE）、SwiGLU FFN（vs ReLU）、RMSNorm（vs LayerNorm）、Pre-Norm（vs Post-Norm）、GQA（70B）。这些改进使 LLaMA 在训练稳定性、效果和推理效率上都优于早期 GPT。

### Q9: MLA（Multi-head Latent Attention）的原理？
**答**：DeepSeek-V2 提出的 MLA 将 KV 投影到低维潜在空间再缓存，推理时从潜在空间恢复。相比 GQA 固定分组，MLA 通过学习的压缩更灵活，KV Cache 压缩比更高，且对模型效果影响更小。

### Q10: Transformer 处理长文本的挑战和解决方案？
**答**：挑战是 O(n^2) 的计算和内存复杂度。解决方案包括：稀疏注意力（Sliding Window、Longformer）、FlashAttention（IO 优化）、位置编码外推（RoPE + YaRN/NTK）、GQA/MQA 减少 KV Cache、分块处理、Ring Attention（分布式长序列注意力）。
