执行范围以 [RESUME_POLICY.md](../RESUME_POLICY.md) 为准；本合同只校验本轮新写v4标注，不强制改写历史复用结果。新增质量要求见 [QUALITY_REVISION_4.md](QUALITY_REVISION_4.md)。

# dots 新输出数据契约（准备包修订4）

D01–D13结论见原字节保留的 [PENDING_DECISIONS.md](PENDING_DECISIONS.md)。其中“实施状态与版本”是修订3历史快照；当前版本、执行范围和v4新增字段以本合同、RESUME_POLICY及QUALITY_REVISION_4为准，历史“1200步骤仍按原路线”不作为本轮执行指令。当前仅供本地检查，处理、上传及数据库权限均为 false。

## 唯一合同与版本

新写入口为冻结 `rules/runtime/astrology_corpus/annotation/output_validation.py` 的 `validate_annotation_output`；组合来源使用 `validate_combination_output`。显式别名归一化使用 `normalize_annotation_terms`，失败项保留并更正，不丢未知值。JSON Schema 由同一入口导出，检查形状/枚举；Python 再检查字段关系、精确证据偏移及语境。基础模型仅负责历史读取，不能代替新写校验。

三版本戳：vocab_version 和 school_vocab_version 为本流派 YAML 的 `_meta.version`（紫微2.1.0、六爻2.0.0、塔罗2.0.0、西占3.0.0）；annotation_version=dots-all-sources-2026-10-13-v4。基础库版本为2.1.0，运行标识按本包覆盖且投影原样保留。版本/hash在 copy_inventory 和 generated_contracts 登记，Git HEAD 只代表基线，代码含本地未提交改动。

Python要求与当前项目一致，为3.10及以上；本地核验使用3.12.11。依赖由 [requirements.txt](requirements.txt) 固定为 Pydantic2.12.5、PyYAML6.0.2；该文件从包根目录访问的路径是 `rules/requirements.txt`。runtime 包含 shared parser、全部标注依赖和历史迁移数据。新破坏性语义迁移仅登记规则，不自动改历史语料。两份原输入 YAML 保持旧字节；它们与新版词表规则的职责见 ARTIFACT_CONTRACT。

## 普通标注的执行规则

每个非空领域字段提供 annotation_evidence：field_path用点分索引，spans含当前chunk_text原句与字符偏移，scope含编号与concept/rule/conditional/case，必要时带原文condition和attribution。同一标量不能混合不同scope。不同案例的数组成员分别举证；明确来源属性与固定查表派生分开。四状态null须独立null_reason；紫微/西占空chart_type亦须说明。对应可执行模型见 semantic_models.py，跨字段检查见 semantic_validation.py。

四状态未提及/不适用为null；概念段落不能默认为本卦、动爻或逆行。盘类、主题、知识领域和方法各自多值；单值只兼容有依据的主类。紫微关系和比较方法分开，马头带剑/箭独立ID；六爻角色/六亲/爻序/阴阳分开，原文宫与derived_palace分开；塔罗提及/使用/介绍完整度分开，三张无牌阵不等于三张牌阵。relations类型和参与实体类型/名称使用当前流派 YAML，参与对象、位置、条件和否定保留在同一关系记录。

中文summary最多300个Unicode字符，英文summary_en最多150个空白分隔词，超限重写。中英文摘要和关键词均非空，英文栏不含CJK。普通输出与04的五字段/行数完全相同。组合模型无普通摘要、页码/index或四派扩展，按独立Schema保留原组合及来源事实；证据写S5报告，不强塞普通字段。

S5报告的逐块field_evidence、两份文件hash和S6复核要求见 [ARTIFACT_CONTRACT](../ARTIFACT_CONTRACT.md)。unknown值、缺证据、条件冲突、版本错配必须改正或记录blocked，不凭默认值通关。

## 只读检查命令

命令均从包根目录执行，并使用已经安装上述依赖的独立Python环境。系统自带Python不保证具备依赖，不能直接把缺少PyYAML当作包内模块缺失。未来环境准备时，在激活的独立环境中执行 `python3 -m pip install -r rules/requirements.txt`；本次未替云端安装或启动任何环境。环境不具备安装条件时记录blocked，不回退到仓库代码。

再用合成样例验证包内依赖，不处理语料：

```sh
python3 -B tools/check_runtime.py
```

以下只在用户另行授权启动且新04/05/S5报告已生成后执行：

```sh
python3 -B tools/validate_output.py deliveries/<run_id>/05_annotated/<school>/<source_id>.jsonl --chunks deliveries/<run_id>/04_chunked/<school>/<source_id>.jsonl --source-id <source_id> --annotation-report deliveries/<run_id>/reports/<source_id>/S5_annotation.json
```

工具通过隔离进程载入包内runtime，关闭editable仓库回退。核对结构、词表、证据偏移及语境、版本、来源/ID/行数/正文、S5证据镜像和文件hash；四份返修另核对04原hash与annotation_changes，格式见RESUME_POLICY。通过不代表所有证据的语义判断正确，S6仍须复核源事实、lineage、coverage和生成来源旁路记录。云端环境与真实召回提升尚未验证，D12评估未启动。

新payload投影保留semantic_facts、semantic_objects、semantic_relations及直接/派生术语；严格约束按同一对象匹配，普通检索保留正文候选。旧数据库缺新字段，不自动改写或启用迁移。两个glossary来源的额外事实仍保存在source_metadata，不能据本包校验结果直接替换生产。

rules/upstream与references仅供查证，旧字段示例以本合同为准。preparation/build_package.py为初始修订构建脚本，不用于重建修订4。
