# **Mercatto Varejo — Registro de Decisões de Transformação (RDT)**

**RDT-01 a RDT-15  \|  SEMANAS 2, 3 E 4**

**Equipe:** Gabriel Siqueira Matias (2023278440020); Krystyan Douglas Santos Costa (2023278440026); Pedro Alexandre Souza de Campos (2023278440034)

**Disciplina:** Mineração de Dados — IFMT

**Data de consolidação:** XX/10/2026

**Versão do pipeline:** 02\_transformacao.py \+ 03\_carga\_dw.py \+ 04\_abt.py

**Repositorio do github:**

https\://github.com/Gabriel-Siqueira-Matias/Atividade-2-Mineracao-de-Dados

# **Finalidade e critérios do registro**

Este documento reúne as fichas obrigatórias RDT-01 a RDT-15, respeitando o padrão do professor: evidência quantificada; alternativas; decisão; justificativa; informação perdida; quantidade e percentual afetados; e asserção ou conferência associada.

Os resultados observados durante a execução atual são distinguidos das auditorias anteriores e de decisões ainda não implementadas. As RDT-­14 e RDT­-15 registram as decisões da Semana 4 e o experimento de vazamento, a RDT-10 a RDT-14 é referente a semana 3 e as demais RDTS (1 a 9\) são ligadas a semana 2\.

# **Resumo da execução verificada**

| Indicador | Resultado |
| :---- | :---- |
| dim\_cliente / dim\_produto | 115.674 / 15.000 |
| dim\_status / dim\_canal | 9 / 3 |
| fato\_pedido / fato\_item\_pedido | 410.000 / 1.371.876 |
| fato\_atendimento / fato\_avaliacao | 95.000 / 160.000 |
| Pedidos recuperados por CPF | 4.290 / 4.290; 0 divergências |
| Asserções Etapas 02 e 03 | 4 / 4 \+ 4 / 4 aprovadas |
| Recarga e conferência completas | 39,7 s |

# **Fichas — RDT-01 a RDT-13**

# 

# **RDT-01  \|  Carga completa ou incremental**

**Etapa do pipeline:** 01\_extracao.py — extrai\_mysql, extrai\_postgres, extrai\_sac, extrai\_avaliacoes

**O que o dado mostrava:** As fontes não disponibilizam CDC ou uma coluna de última alteração confiável e uniforme. Na extração observada foram lidos 577.336 registros ERP, 1.372.388 de e-commerce, 96.140 SAC e 160.000 avaliações: 2.205.864 registros distribuídos entre as quatro fontes.

**Alternativas consideradas:**

**1\. Carga incremental baseada em data/CDC.** menor tráfego, mas detecção de alterações e exclusões incompleta com as informações disponíveis.

**2\. Carga completa em lotes.** reconstrói o staging de forma verificável, ao custo de reler registros inalterados.

**Decisão:** Alternativa 2: extração completa; substituição da tabela na primeira gravação e anexação dos blocos seguintes.

**Justificativa:** As fontes são finitas no laboratório e a recuperação integral simplifica a reexecução e a auditoria. A leitura em blocos limita o uso de RAM.

**O que se perde:** Não permanece no staging um histórico de versões anteriores; paga-se o custo da releitura integral. Não há descarte intencional dos registros de origem.

**Registros afetados:** 2.205.864/2.205.864 linhas reextraídas (100%); descarte deliberado de linhas na extração: 0 (0%).

**Asserção associada:** Contagens origem × stg para cada tabela; QA da extração. Esses controles não medem alterações históricas entre execuções.

# **RDT-02  \|  Unificação de clientes entre ERP e e-commerce**

**Etapa do pipeline:** 02\_transformacao.py — unifica\_clientes

**O que o dado mostrava:** O ERP possui 47.250 cadastros e o e-commerce 85.000 (132.250 entradas). No diagnóstico, foram encontrados 11.470 CPFs válidos distintos em comum; 4.423 CPFs vazios no ERP e 6.855 no e-commerce, além de CPFs com dígitos verificadores inválidos. A execução atual informou 115.674 clientes na dimensão.

**Alternativas consideradas:**

**1\. Unir somente por CPF textual.** parearia números mascarados, inválidos ou compartilhados indevidamente.

**2\. CPF válido, seguido de nome/cidade/UF de correspondência 1:1.** aumenta a cobertura com uma regra mais conservadora para casos sem CPF confiável.

**3\. Não unificar.** evita falsos positivos, mas fragmenta o histórico do mesmo consumidor.

**Decisão:** Alternativa 2: deduplicar CPFs válidos, unir cadastros compatíveis e aplicar pareamento nome/localidade somente em relações 1:1; demais cadastros permanecem separados.

**Justificativa:** Prioriza não fundir homônimos e permite representar consumidores entre sistemas. Cada dim\_cliente tem um cliente\_sk; a procedência é marcada por LEGADO, ECOM ou AMBOS.

**O que se perde:** A similaridade textual não garante identidade e pode deixar um mesmo cliente com várias linhas (falso negativo), ou unir homônimos exatos (falso positivo). A linha da dimensão não preserva todos os IDs secundários descartados.

**Registros afetados:** 132.250 cadastros de entrada (100%); dimensão da execução atual: 115.674 linhas — redução líquida de 16.576 linhas (12,53%), não equivalendo automaticamente a 16.576 pessoas duplicadas.

**Asserção associada:** 02: “CPF valido sem duplicidade” e “origem\_unificacao no dominio”, ambas aprovadas; “pedidos x clientes sem multiplicacao” é controle complementar.

**Nota de transparência:** O diagnóstico conservador anterior sugeria 119.480 clientes; esse valor veio de uma simulação/implementação diferente e não substitui o total observado na execução atual.

# **RDT-03  \|  Duplicatas internas de clientes do legado**

**Etapa do pipeline:** 02\_transformacao.py — unifica\_clientes

**O que o dado mostrava:** No legado foram identificados 1.302 CPFs válidos repetidos, envolvendo 2.604 cadastros. O diagnóstico informa nomes normalizados consistentes nos grupos repetidos; existem IDs diferentes para o mesmo CPF.

**Alternativas consideradas:**

**1\. Manter todos os cadastros na dimensão.** preserva cada ID, mas fragmenta frequência e recorrência de um consumidor.

**2\. Deduplicar por CPF válido, mantendo a primeira linha.** gera um cadastro representativo por CPF, mas perde o ID secundário como coluna direta da dimensão.

**Decisão:** Alternativa 2: ordenar por ID de origem e manter o primeiro registro com CPF válido por grupo; recuperar SK dos pedidos por CPF normalizado quando o ID não estiver na dimensão.

**Justificativa:** A unificação evita duplicação de pessoas na métrica de clientes; a correção posterior de pedidos impede o vínculo arbitrário com o primeiro cliente da dimensão.

**O que se perde:** O ID secundário deixa de constar na linha representativa de dim\_cliente; é recuperável no staging, mas a dimensão não contém um mapa completo persistido de todos os IDs originais.

**Registros afetados:** 2.604/47.250 cadastros (5,51%) participam dos 1.302 grupos repetidos; 1.302 IDs excedentes (2,76% do legado) não são retidos como ID principal desses grupos.

**Asserção associada:** 02: “CPF valido sem duplicidade” (aprovada). Auditoria complementar da RDT-12 comprovou a recuperação de pedidos por CPF, não a permanência de todos os IDs.

# **RDT-04  \|  Conformação dos status, inclusive payment\_declined**

**Etapa do pipeline:** 02\_transformacao.py — conforma\_status

**O que o dado mostrava:** Quatro status distintos no ERP e cinco no e-commerce formam nove pares (sistema, status). O diagnóstico cobre 410.000 pedidos; payment\_declined aparece em 13.990 pedidos ECOM (5,00% do ECOM).

**Alternativas consideradas:**

**1\. Apenas renomear as strings.** mantém categorias inconciliáveis entre sistemas e mistura estados operacionais.

**2\. Dimensão de status com categoria geral e motivo.** permite comparação de processos e conserva a origem e o motivo quando conhecido.

**Decisão:** Alternativa 2: popular nove linhas em dim\_status, incluindo payment\_declined → CANCELADO / RECUSA\_PAGAMENTO; os demais estados são agrupados em CONCLUIDO, CANCELADO, EM\_ANDAMENTO ou EM\_PROCESSAMENTO.

**Justificativa:** Pagamento recusado é um tipo de cancelamento de pedido distinto do cancelamento comum. Reter o status\_origem ajuda a explicar o agrupamento.

**O que se perde:** Ao agregar só status\_conformado, perde-se a especificidade operacional. O motivo CANCELADO\_CLIENTE atribuído a canceled ECOM no código deve ser tratado com cautela, pois a string canceled isolada não comprova iniciativa do cliente.

**Registros afetados:** 410.000/410.000 pedidos (100%) cobertos por nove regras; 13.990 payment\_declined (3,41% de todos os pedidos).

**Asserção associada:** Conferência de nove pares distintos e zero status sem regra nas auditorias; a Etapa 3 testa FK válida de fato\_pedido e a contagem total de pedidos.

# **RDT-05  \|  Semântica dos totais do ERP e do e-commerce**

**Etapa do pipeline:** 02\_transformacao.py — conforma\_valores**s**

**O que o dado mostrava:** O ERP informa VALOR\_TOTAL já com frete: soma R\$ 281.084.957,13. No e-commerce, total\_amount exclui frete: soma R\$ 734.859.099,95; frete R\$ 10.991.760,38, portanto total padronizado R\$ 745.850.860,33. No ERP havia 4.003 divergências antes do tratamento de desconto-sentinela e zero depois.

**Alternativas consideradas:**

**1\. Usar os totais brutos como comparáveis.** trataria valores com semânticas diferentes como se fossem equivalentes.

**2\. Recompor por itens, descontos e frete conforme.** cria uma medida comum: mercadoria \+ frete − desconto, conciliada com a origem normalizada.

**Decisão:** Alternativa 2: valor\_total \= valor\_mercadoria \+ valor\_frete − valor\_desconto; para conferir ECOM, adicionar o frete a total\_amount da origem; no ERP não somá-lo novamente.

**Justificativa:** A fórmula comum evita superestimar um sistema e subestimar outro por diferença na representação do frete.

**O que se perde:** A medida padronizada não mostra diretamente a semântica particular do total bruto. Reconstituir o valor original exige consultar o staging e a regra aplicada.

**Registros afetados:** 410.000 pedidos (100%) têm valor padronizado; 4.003/130.000 pedidos ERP (3,08%) divergiam antes da correção da sentinela e 0 depois, segundo o diagnóstico.

**Asserção associada:** 02: “pedidos divergentes de itens (\> R\$0,01)” \= 0; 03: “reconciliacao financeira (LEGADO e ECOM, ±0,1%)” \= 0 origens divergentes.

# **RDT-06  \|  Frete negativo e desconto sentinela −1**

**Etapa do pipeline:** 02\_transformacao.py — conforma\_valores; 03\_carga\_dw.py — carrega\_fato\_item

**O que o dado mostrava:** Foram registrados 362 pedidos ERP e 812 pedidos ECOM com frete negativo (total 1.174). No ERP, 4.056 dos 397.086 itens possuem VL\_DESCONTO \= −1, distribuídos em 4.003 pedidos. Não se encontrou outro desconto negativo no diagnóstico das origens.

**Alternativas consideradas:**

**1\. Manter os sinais como valores financeiros literais.** preserva o bruto, porém fretes negativos e desconto −1 podem contaminar a soma sem regra definida.

**2\. Padronizar frete por valor absoluto e substituir desconto −1 por zero no cálculo.** restaura as conciliações, mas interpreta uma anomalia como valor financeiro não negativo.

**3\. Separar status desconhecido/estorno em campos auxiliares.** preserva a anomalia explicitamente, exigindo modelagem adicional.

**Decisão:** Alternativa 2 na implementação atual: abs(frete) e desconto −1 → 0 nas medidas derivadas; staging permanece inalterado.

**Justificativa:** A soma financeira fechou após tratar a sentinela. A escolha de abs(frete) operacionaliza a comparação, mas NÃO prova que o sinal negativo signifique mero erro: pode representar crédito/estorno e merece revisão posterior.

**O que se perde:** Perde-se o sinal original do frete e a distinção entre desconto realmente zero e sentinela desconhecida na fato (o stg conserva ambos). Não existe indicador específico de anomalia nos campos atuais.

**Registros afetados:** 1.174/410.000 pedidos com frete negativo (0,29%); 4.056/397.086 itens ERP com sentinela (1,02%), atingindo 4.003/130.000 pedidos ERP (3,08%).

**Asserção associada:** Conferência financeira por sistema (03, tolerância ±0,1%): aprovada. Contagens de frete negativo e sentinelas são diagnósticos complementares, não asserções oficiais independentes.

# **RDT-07  \|  Categorias de produto e casos ambíguos**

**Etapa do pipeline:** 02\_transformacao.py — conforma\_categorias

**O que o dado mostrava:** Há 3.000 produtos ERP com rótulos livres e 12.000 produtos ECOM associados a taxonomia de 21 categorias. A auditoria específica ERP encontrou 661 rótulos ambíguos ou não informativos (321 ambíguos e 340 não informativos), enquanto os 12.000 produtos ECOM tinham referências válidas.

**Alternativas consideradas:**

**1\. Manter somente categorias originais.** preserva nuance, mas impede comparações diretas entre os sistemas.

**2\. Agrupar por regras em categoria\_nivel1 e classe residual OUTROS.** permite análise comum, com perda de precisão e possíveis agrupamentos indevidos.

**3\. Deixar categoria ausente quando ambígua.** evita atribuição arbitrária, mas aumenta nulos nas análises por categoria.

**Decisão:** Alternativa 2 na implementação corrente: classificar por palavras-chave (eletro, info, games, telefonia etc.) e colocar não reconhecidos em OUTROS; as categorias originais permanecem no staging.

**Justificativa:** Uma categoria de nível 1 é adequada para analisar sortimento no DW; a classe OUTROS evita imputar uma subcategoria específica que a fonte não garante.

**O que se perde:** Perde-se a subcategoria detalhada na visão agregada e rótulos distintos podem ser fundidos. Atenção: 661 produtos ambíguos/não informativos foram identificados por diagnóstico anterior; a regra final por substring não demonstra tratar esses 661 de modo idêntico à simulação conservadora.

**Registros afetados:** 15.000/15.000 produtos (100%) carregados na dimensão; 661/3.000 produtos ERP (22,03%) merecem cautela por categoria ambígua ou não informativa no diagnóstico.

**Asserção associada:** Conferência de 15.000 produtos carregados; auditoria da taxonomia verificou 21 categorias de referência e 12.000 produtos ECOM válidos. A Etapa 3 confere dim\_produto não vazia, mas não testa semanticamente a classificação.

# **RDT-08  \|  Ausências, datas invertidas e sentinelas**

**Etapa do pipeline:** 02\_transformacao.py — trata\_datas\_e\_ausencias; 03\_carga\_dw.py — carrega\_fatos\_auxiliares

**O que o dado mostrava:** Nos 130.000 pedidos ERP: 28.808 entregas nulas, 1.025 com sentinela 1900-01-01 e 775 entregas anteriores ao pedido (excluída a sentinela). Nos 280.000 ECOM: 72.796 entregas nulas e 1.683 invertidas. No SAC: 5.856 fechamentos e 8.682 notas ausentes. No marketplace: 4.831 avaliações sem campo rating.

**Alternativas consideradas:**

**1\. Substituir ausências por datas/notas artificiais.** produz prazos e opiniões não observados, gerando falsa certeza.

**2\. Preservar NULL e anular datas comprovadamente inválidas.** não inventa informação e mantém contagem de pedidos, embora impeça algumas métricas.

**3\. Excluir os pedidos incompletos.** reduz viés por dados artificiais, mas descarta compras válidas.

**Decisão:** Alternativa 2: entregas anteriores ao pedido ou anteriores a 2000 tornam-se NULL em dw.fato\_pedido; dias\_entrega só é calculado para datas coerentes. Em SAC, fechamento ou satisfação ausente permanecem NULL; rating ausente não vira nota zero.

**Justificativa:** Ausência de entrega pode significar pedido ainda não entregue; sentinelas e inversões impedem cálculo confiável de prazo. É melhor não produzir um número fictício.

**O que se perde:** Perdem-se prazos calculáveis para pedidos sem data válida; não é possível distinguir todas as causas das ausências sem indicadores adicionais. O valor inválido original só permanece no stg.

**Registros afetados:** 101.604/410.000 pedidos (24,78%) sem data de entrega; 3.483/410.000 (0,85%) com data-sentinela ou invertida. SAC: 5.856/96.140 (6,09%) sem fechamento e 8.682/96.140 (9,03%) sem nota. Avaliações: 4.831/160.000 (3,02%) sem rating.

**Asserção associada:** 03: “nenhum pedido com dias\_entrega negativo” \= 0\. Os diagnósticos anteriores conferem as classes de ausência; as quatro asserções atuais não medem separadamente nulos do SAC e marketplace.

# **RDT-09  \|  Padronização e limites de fuso horário**

**Etapa do pipeline:** 02\_transformacao.py — conforma\_valores / trata\_datas\_e\_ausencias; 03\_carga\_dw.py — carrega\_fato\_avaliacao

**O que o dado mostrava:** ERP usa timestamp sem fuso; e-commerce usa timestamp com fuso. A comparação da data civil em UTC versus America/Cuiaba alteraria 46.530 datas de pedido e 34.621 de entrega do e-commerce. Avaliações: 128.085 timestamps com fuso explícito e 31.915 sem fuso (160.000 no total).

**Alternativas consideradas:**

**1\. Ignorar offset e truncar sempre os primeiros dez caracteres.** simples, mas pode representar o dia local em vez do dia UTC.

**2\. Converter timestamps com offset para UTC, sem atribuir fuso a valores naïve.** explicita instantes comparáveis e reconhece incerteza do legado/NDJSON sem fuso.

**3\. Assumir automaticamente America/Cuiaba para todas as fontes.** altera instantes sem evidência de que todos os sistemas operavam no mesmo fuso.

**Decisão:** Política analítica recomendada: alternativa 2\. O código da transformação usa conversão UTC em parte da geração de datas, mas o carregamento atual de avaliações extrai LEFT(submitted\_at, 10), preservando a data escrita, sem conversão temporal uniforme. Portanto, a padronização total AINDA NÃO ESTÁ DEMONSTRADA pela execução.

**Justificativa:** O diagnóstico estabelece a diferença entre timestamp com e sem fuso; não é legítimo imputar offset aos 31.915 registros sem essa informação. O registro explicita a limitação da versão atual, em vez de afirmar uma normalização universal inexistente.

**O que se perde:** Ao reduzir um timestamp a DATE, perdem-se hora e offset; a data civil pode variar conforme o fuso escolhido. Para campos sem fuso, a conversão exata de instantes não pode ser comprovada.

**Registros afetados:** E-commerce: 46.530/280.000 pedidos (16,62%) e 34.621/280.000 entregas (12,36%) teriam dia diferente entre UTC e Cuiabá; avaliações: 128.085/160.000 (80,05%) com offset e 31.915/160.000 (19,95%) sem offset explícito.

**Asserção associada:** Conferência diagnóstica de 160.000 avaliações com/sem fuso e dos tipos temporais ERP/ECOM. Não existe, nas quatro asserções oficiais atuais, teste que assegure equivalência completa de fuso em todas as fatos.

**Nota de transparência:** PENDÊNCIA TÉCNICA DOCUMENTADA: a regra integral de UTC deve ser concluída se a disciplina exigir timestamps comparáveis e não apenas datas civis em fatos.

# **RDT-10  \|  Itens de pedido órfãos**

**Etapa do pipeline:** 03\_carga\_dw.py — carrega\_fato\_item

**O que o dado mostrava:** A origem apresenta 397.086 itens ERP e 995.367 ECOM: 1.392.453 itens brutos. Desse universo, 5.868 ERP e 14.709 ECOM não encontram pedido na fonte de pedidos. A execução posterior carregou 1.371.876 linhas em dw.fato\_item\_pedido.

**Alternativas consideradas:**

**1\. Inserir todos os itens com pedido\_sk nulo.** incompatível com a FK/NOT NULL da fato de itens.

**2\. Criar pedidos fictícios para órfãos.** preservaria linhas na fato, mas fabricaria compras.

**3\. Manter os órfãos no stg e carregar apenas vinculáveis.** protege a integridade da fato e conserva evidência bruta para investigação.

**Decisão:** Alternativa 3: 20.577 itens sem pedido não entram em fato\_item\_pedido, mas permanecem preservados no staging. A junção com produto também é validada por contagem de elegíveis.

**Justificativa:** Os pedidos do DW só devem receber itens cuja origem seja comprovável. A retenção no staging permite reprocessar se a fonte for corrigida.

**O que se perde:** Os itens órfãos não entram nas análises de itens no DW, podendo subestimar as quantidades brutas de produtos; não são apagados da origem nem do staging.

**Registros afetados:** 20.577/1.392.453 itens (1,48%) não carregados; 5.868/397.086 ERP (1,48%) e 14.709/995.367 ECOM (1,48%). 1.371.876/1.392.453 (98,52%) foram carregados.

**Asserção associada:** Validação específica do script de carga: contagem dos órfãos e comparação do número inserido com itens elegíveis. As quatro asserções oficiais de Etapa 3 não substituem essa validação.

# **RDT-11  \|  Grão de fato\_pedido e fato\_item\_pedido**

**Etapa do pipeline:** 03\_carga\_dw.py — carrega\_fato\_pedido / carrega\_fato\_item

**O que o dado mostrava:** Os pedidos de origem são 130.000 ERP \+ 280.000 ECOM \= 410.000. As fontes de item possuem 1.392.453 registros, dos quais 1.371.876 têm pedido vinculável. Uma junção de pedidos a itens multiplicaria medidas de frete ou total se o grão fosse confundido.

**Alternativas consideradas:**

**1\. Uma única fato detalhada por item, repetindo o total do pedido.** duplicaria totais ao somar os itens.

**2\. Fatos distintas no grão de pedido e no de linha de item.** separa medidas próprias de cada entidade e liga a linha de item ao pedido por pedido\_sk.

**Decisão:** Alternativa 2\. Declarações exigidas: “Uma linha de dw.fato\_pedido representa um pedido individual de um sistema de origem.” “Uma linha de dw.fato\_item\_pedido representa uma linha de item de pedido vinculável, referente a um produto.”

**Justificativa:** A dimensão de produto participa da fato de item; valor\_total e frete do pedido não são repetidos como totais em cada linha de produto.

**O que se perde:** Na fato de itens, o ID natural de linha de origem não foi persistido, limitando a reconciliação linha a linha sem voltar a stg; itens órfãos são excluídos conforme RDT-10.

**Registros afetados:** 410.000/410.000 pedidos (100%); 1.371.876/1.392.453 itens (98,52%) no DW; 20.577 itens (1,48%) fora da fato por ausência de pedido.

**Asserção associada:** 03: “contagem total de fato\_pedido” \= 410.000; validação adicional da contagem de itens elegíveis \= 1.371.876.

# **RDT-12  \|  Pedidos sem cliente correspondente e recuperação por CPF**

**Etapa do pipeline:** 02\_transformacao.py — conforma\_valores (correção); 03\_carga\_dw.py — recarga de fatos auxiliares

**O que o dado mostrava:** A auditoria do DW encontrou 3.930 pedidos LEGADO e 360 ECOM sem correspondência pelo ID original na dimensão (4.290/410.000). Todos os cadastros existem nas fontes. Na versão anterior, os 4.290 pedidos estavam atribuídos ao primeiro cliente real da dimensão (cliente\_sk=1). A consulta por CPF identificou candidatos para os 4.290; a transformação corrigida informou 3.930/3.930 e 360/360 recuperados.

**Alternativas consideradas:**

**1\. Descartar os pedidos sem mapeamento direto.** reduziria históricos e alteraria as métricas financeiras.

**2\. Preencher cliente\_sk com o primeiro cliente real da dimensão.** evita FK nula, mas imputa milhares de compras à pessoa errada; foi a regra anterior, revogada.

**3\. Recuperar SK pelo CPF de 11 dígitos único na dimensão, mantendo preferência pelo ID.** preserva compras sem criar associações arbitrárias; falha se não há candidato inequívoco.

**Decisão:** Alternativa 3 IMPLEMENTADA. Primeiro vínculo por id\_legado/id\_ecommerce; em falha, CPF extraído do cadastro original e normalizado, aceito somente com 11 dígitos e SK único. Se persistir a falta de candidato, interromper a transformação; não reaplicar fallback arbitrário.

**Justificativa:** A dim\_cliente manteve 115.674 linhas e a fato preservou os 410.000 pedidos. A correção foi reexecutada, seguida da carga da Semana 3\. Consulta pós-carga mostrou LEGADO 3.930/3.930, ECOM 360/360 e zero divergências frente ao CPF único da dimensão.

**O que se perde:** Os IDs secundários ainda não estão todos materializados na linha da dimensão (continua necessário consultar stg para recompor a linhagem). CPF com 11 dígitos único na dimensão não prova, sozinho, identidade real nem validade dos dígitos verificadores; há risco residual a registrar.

**Registros afetados:** 4.290/410.000 pedidos (1,05%) recuperados: 3.930/130.000 LEGADO (3,02%) e 360/280.000 ECOM (0,13%). Identificadores distintos afetados: 1.326 LEGADO e 103 ECOM (1.429 IDs, não necessariamente pessoas distintas). Divergências pós-correção na auditoria: 0/4.290 (0%).

**Asserção associada:** 02: quatro asserções passaram, incluindo ausência de multiplicação (410.000) e CPF válido sem duplicidade. Auditoria ADICIONAL específica confirmou 4.290 associações via CPF único, com zero SK divergentes; não é uma das quatro asserções oficiais.

**Nota de transparência:** A correção evita o fallback arbitrário. Ela ainda deve ser documentada como pareamento técnico condicionado, não como prova jurídica de identidade do consumidor.

# **RDT-13  \|  Atendimentos SAC e avaliações sem vínculo com pedido**

**Etapa do pipeline:** 02\_transformacao.py — liga\_sac\_e\_avaliacoes (diagnóstico); 03\_carga\_dw.py — carrega\_fatos\_auxiliares

**O que o dado mostrava:** O staging possui 96.140 linhas SAC, 95.000 protocolos distintos e 160.000 avaliações com review\_id único. A execução carregou 95.000 atendimentos e 160.000 avaliações. A consulta pós-carga contou 95.000/95.000 atendimentos com pedido\_sk e 160.000/160.000 avaliações vinculadas, zero sem pedido.

**Alternativas consideradas:**

**1\. Excluir registros que não encontrem pedido.** perderia histórico legítimo de satisfação ou atendimento.

**2\. LEFT JOIN pela referência composta, mantendo nulo quando faltar pedido.** preserva o fato auxiliar sem fabricar associação.

**3\. Criar pedido sintético.** preserva FK preenchida, mas introduz um pedido inexistente.

**Decisão:** Alternativa 2 IMPLEMENTADA: SAC localiza o pedido com o sistema \+ ID (LEG- ou ECOM-), consolida em um protocolo por linha; avaliações ligam order\_ref a id\_origem. A referência ao fato de pedido tem prioridade sobre um pareamento pelo CPF do atendimento.

**Justificativa:** A etapa preserva informação quando a referência está ausente, e o esquema permite pedido\_sk nulo nas fatos auxiliares. Na execução informada, nenhuma referência ficou sem pedido.

**O que se perde:** A consolidação por protocolo não transporta todas as 1.140 linhas adicionais do SAC para a fato; permanecem em stg. O CPF do SAC pode divergir do cadastro do pedido (diagnóstico anterior indicou 88.011 divergências entre 88.012 comparáveis), sem que isso justifique mudar automaticamente o pedido vinculado. A presença de SKU não comprova que o produto era item daquele pedido.

**Registros afetados:** SAC: 95.000/95.000 protocolos ligados (100%), 0 sem pedido; 1.140/96.140 linhas brutas (1,19%) não repetidas na fato. Marketplace: 160.000/160.000 reviews ligados (100%), 0 sem pedido.

**Asserção associada:** Auditoria automática após o COMMIT: auxiliares sem pedido (SAC=0, avaliações=0), contagens 95.000 e 160.000 e 4/4 asserções da carga aprovadas. Essa conferência de vínculo é complementar ao 3.5.

**RDT-14 \| Desenho dos atributos da ABT e verificação de ausência de vazamento**  
**Etapa do pipeline:** 04\_abt.py — SQL\_ALVO, SQL\_ATRIBUTOS\_CORRETOS, monta\_atributos\_corretos e verifica\_amostra\_corte.

**O que o dado mostrava:** A dimensão contém 115.674 clientes. Destes, 108.064 (93,42%) possuem ao menos um pedido até 30/06/2025 e formam a população da ABT; 7.610 (6,58%) não entram nessa população. Na janela de 01/07 a 28/09/2025, 24.043 clientes (22,25%) fizeram compra não cancelada e 84.021 (77,75%) não fizeram. O DW permite calcular histórico de compras, itens, SAC e avaliações; porém não oferece histórico completo de alterações de status/categoria.

**Alternativas consideradas:**   
**1\. Usar pedidos de todo o período ao calcular atributos.** Simplificaria agregações, mas faria as features conhecerem compras ocorridas após a data da previsão.   
**2\. Construir uma linha por cliente elegível e limitar os atributos preditivos a eventos até 30/06/2025.** Preserva a cronologia exigida e separa as compras futuras para formar apenas o alvo.  
**3\. Utilizar apenas características cadastrais atuais e todas as sugestões do professor, inclusive o canal predominante.** Seria simples, mas perderia comportamento transacional e poderia carregar informações pós-corte ou canal impreciso. 

**Decisão:** Alternativa 2, implementada. Foram selecionadas 15 features históricas (10 sugestões aplicáveis \+ 5 adicionais). O grão é um cliente\_sk por linha; cliente\_sk e data\_corte ficam como metadados e alvo\_recompra\_90d como classe binária. Compras na janela futura definem exclusivamente o alvo. Não foi utilizado canal\_predominante, porque a dimensão de canais está simplificada em três registros e não comprova o canal real de cada pedido.

**Justificativa:** Atributos históricos capturam recência, frequência, valor, variedade, regularidade, atendimento e satisfação, enquanto a separação temporal evita a forma explícita de vazamento por compra futura. As cinco features adicionais aumentam a variedade de hipóteses testadas sem depender de fontes novas. O corte é inclusivo (p.data\_pedido\_sk ≤ 30/06/2025).

**As 15 features e suas regras:**

| Feature  | Regra aplicada  | Ausência ou restrição |
| :---- | ----- | ----- |
| recencia\_dias  | Dias desde a última compra não cancelada até o corte.  | NULL se não houve  compra válida. |
| frequencia\_pedidos  | Pedidos não cancelados até o corte.  | 0 se nenhuma compra válida. |
| valor\_monetario  | Soma de valor\_total dos pedidos não cancelados.  | 0 se sem compras válidas. |
| ticket\_medio  | Média do valor\_total dos pedidos não cancelados.  | NULL se não há compras válidas. |
| prazo\_medio\_entrega  | Média de dias\_entrega dos concluídos e entregues até o corte. | NULL se sem prazo  válido. |
| taxa\_cancelamento  | Pedidos cancelados / total de pedidos históricos.  | Proporção entre 0 e 1\. |
| categorias\_distintas  | Categorias nível 1 distintas em itens vinculados a pedidos válidos. | 0 quando não existem itens elegíveis. |
| qtd\_atendimentos\_sac  | Quantidade de protocolos abertos até o corte por cliente\_sk.  | 0 se sem atendimento. |
| nota\_media\_avaliacoes  | Média das avaliações com data e pedido até o corte.  | NULL se sem nota  observada. |
| tempo\_como\_cliente  | Dias entre a primeira compra histórica e o corte.  | Inclui primeira compra  cancelada. |
| pedidos\_ultimos\_90d  | Pedidos válidos nos 90 dias encerrados em 30/06/2025.  | 0 se sem compras nesse período. |
| intervalo\_medio\_compras  | (Última data − primeira data)/(nº de pedidos válidos − 1).  | NULL se menos de duas compras. |
| qtd\_total\_itens  | Soma de quantidade dos itens vinculados a compras válidas.  | 0 se sem itens elegíveis. |
| desvio\_valor\_pedidos  | Desvio padrão amostral de valor\_total das compras válidas.  | NULL com menos de duas compras. |
| dias\_desde\_ultimo\_sac  | Dias desde a última abertura de SAC até o corte.  | NULL se sem SAC  anterior. |

**As 15 features e suas regras**

**O que se perde:** A ABT agrega transações em uma linha por consumidor e não reproduz cada evento individual. Exclui atributos derivados de compras posteriores ao corte e deixa fora 7.610 clientes sem pedido anterior, de modo que o modelo não representa “clientes novos” sem histórico. Onde não existe média ou intervalo observável, usa NULL, não zero. A exclusão de canal\_predominante abdica de uma informação potencialmente útil. Status e categorias do DW são fotografias atuais: caso tenham mudado depois de 30/06/2025, o código não possui versionamento que prove sua disponibilidade histórica.

**Registros afetados: 108.064/115.674 clientes** (93,42% da dimensão) formam a ABT; **7.610** (6,58%) ficam fora do grão elegível. São 108.064 linhas × 15 atributos preditivos, 2 metadados e 1 alvo \= **18 colunas** em cada CSV; taxa positiva 24.043/108.064 (22,25%). Não foi quantificada por atributo a quantidade específica de NULL no arquivo de 18 colunas.

**Asserção associada:** A versão correta executou **5/5 asserções aprovadas**: faixa de taxa do alvo \[2%; 60%\]; unicidade de cliente\_sk; cardinalidade da junção alvo × atributos (108.064); ausência de nulos em frequencia\_pedidos; e conferência independente de recência/frequência em **amostra determinística de 100 clientes**, com zero divergências. A última asserção não valida exaustivamente todas as 15 features nem a historicidade de status/categorias.

**RDT-15 \| Vazamentos deliberados na versão de comparação**   
**Etapa do pipeline:** 04\_abt.py — monta\_atributos\_com\_vazamento; 05\_exportar\_orange.py; fluxos Orange (Test & Score, Confusion Matrix e ROC Analysis).

**O que o dado mostrava:** A ABT correta apresentava 108.064 clientes, 15 features e 22,25% de recompra. No teste externo estratificado 70/30, com 32.419 clientes (25.206 classe 0; 7.213 classe 1), os modelos obtiveram AUC modesta (Tree 0,496; Logistic Regression 0,578; Naive Bayes 0,570). O enunciado determina criar um contraexemplo com duas features calculadas sobre a base inteira mais normalização feita antes da divisão treino/teste.

**Alternativas consideradas:**   
**1\. Manter todos os atributos com o corte e ajustar normalização apenas no treino.** É metodologicamente correto, porém não fornece o contraexemplo solicitado.  
**2\. Introduzir explicitamente dois vazamentos temporais e uma normalização global pré-partição, mantendo alvo, população e demais features iguais à versão correta.** Permite medir o contraste, assumidamente inválido para produção.  
**3\. Inserir diretamente o alvo entre as features.** Elevaria artificialmente o resultado, mas tornaria o erro trivial e não refletiria os mecanismos solicitados.

**Decisão:** Alternativa 2, implementada. (1) recencia\_dias usa a última compra não cancelada de toda a fato\_pedido, inclusive após 30/06/2025; pode ficar negativa em relação ao corte. (2) frequencia\_pedidos conta todas as compras não canceladas, inclusive futuras. (3) valor\_monetario permanece somado apenas até o corte, mas é substituído por z-score calculado com média e desvio padrão dos 108.064 clientes antes do particionamento. As outras 12 features, o alvo e a população são mantidos.

**Justificativa:** A comparação evidencia que medir a qualidade de um modelo sem examinar quando cada atributo fica disponível pode premiar uma solução inviável em uso real. A normalização global foi introduzida deliberadamente para avaliar separadamente um vazamento menos óbvio: estatísticas do teste utilizadas na preparação dos dados.

**Resultado do experimento principal no Orange**

| Modelo | AUC correta | AUC vazamento | BA correta | BA vazamento |
| :---- | :---- | :---- | :---- | :---- |
| Tree  | 0,496  | 0,965  | 51,19%  | 94,73% |
| Logistic Regression  | 0,578  | 0,873  | 50,06%  | 65,23% |
| Naive Bayes  | 0,570  | 0,878  | 52,35%  | 78,01% |

BA \= acurácia balanceada \= (sensibilidade da classe 1 \+ especificidade da classe 0)/2, calculada a partir das matrizes de confusão. AUC extraída do Test & Score. Diferenças máximas: Tree \+0,469 de AUC e \+43,54 pontos percentuais de BA.

**Controle complementar: efeito isolado da normalização**

| Modelo  | AUC treino  | AUC global  | BA treino  | BA global |
| :---- | :---- | :---- | :---- | :---- |
| Tree  | 0,509  | 0,509  | 51,3778%  | 51,3778% |
| Logistic Regression  | 0,577  | 0,577  | 50,1108%  | 50,1128% |
| Naive Bayes  | 0,565  | 0,565  | 52,8407%  | 52,8407% |

No teste controlado, treino e teste têm 75.645 e 32.419 clientes idênticos nas duas variantes. Somente valor\_monetario muda de padronização. As AUCs são iguais nas três casas exibidas; na Logistic Regression a BA aumenta aproximadamente 0,0020 p.p., com um falso positivo a menos; Tree e Naive Bayes têm matrizes idênticas. Portanto, não houve ganho perceptível da normalização isolada neste conjunto.

**O que se perde:** Na versão contaminada, recência e frequência deixam de representar informação disponível no instante da predição; a aparente capacidade de generalização ao futuro perde validade operacional. O z-score global perde a escala monetária diretamente interpretável, embora o valor bruto permaneça na ABT correta e no DW. A melhora conjunta não pode ser atribuída individualmente a uma das duas features futuras sem outra ablação. Uma normalização pré-partição pode causar vazamento mesmo sem efeito mensurável na AUC.

**Registros afetados:** 108.064/108.064 clientes (100%) têm valor\_monetario padronizado globalmente na ABT com vazamento; recência e frequência são recalculadas para os mesmos 108.064 clientes, mas nem todas necessariamente mudam de valor. Uma auditoria anterior das duas features temporais, ainda no conjunto de 10 features, encontrou 43.262/108.064 linhas (40,03%) com valores alterados; essa contagem específica não foi reapurada no CSV final de 15 features. A avaliação utilizou 32.419 clientes (30% da ABT) e registrou diferenças quantitativas na AUC e BA acima. 

**Asserção associada:** As duas ABTs passaram seus controles estruturais: versão correta 5/5 e versão com vazamento 4/4 asserções. Na contaminada não se executa a verificação de corte da versão correta, pois a violação é proposital. A verificação de ausência de vazamento é garantida apenas na amostra temporal da versão correta e em inspeção do SQL; a comparação de desempenho foi observada no Orange, não em asserção do banco.

**Tabela-resumo das RDT-01 a RDT-15**

| Ficha  | Decisão  | Registros afetados  | % da base  | Informação  perdida? |
| :---- | ----- | ----- | ----- | ----- |
| RDT-01  | Extração integral em lotes  | 2.205.864 linhas  | 100%  | Versões antigas do staging |
| RDT-02  | CPF válido \+ nome/localidade 1:1 | 132.250 cadastros; 115.674 na dimensão | 12,53% redução  | Risco de pareamento |
| RDT-03  | Deduplicar CPF legado  | 2.604 cadastros; 1.302  excedentes | 5,51% / 2,76%  | IDs secundários na dimensão |
| RDT-04  | Nove status conformados  | 410.000 pedidos  | 100%  | Especificidade ao  agregar |
| RDT-05  | Recompor total financeiro  | 410.000; 4.003 ERP corrigidos  | 100% / 3,08%  ERP | Semântica bruta na fato |
| RDT-06  | abs(frete); desconto \-1→0  | 1.174 fretes; 4.056 itens  | 0,29% / 1,02%  | Sinal e sentinela |
| RDT-07  | Categoria nível 1/OUTROS  | 15.000 produtos; 661 ambíguos ERP | 100% / 22,03%  ERP | Detalhe da categoria |
| RDT-08  | NULL para dados  ausentes/inválidos | 101.604 ausências; 3.483  inválidas | 24,78% / 0,85%  | Prazos sem base  confiável |
| RDT-09  | Política UTC parcial  documentada | 46.530 pedidos; 31.915 reviews sem fuso | 16,62% / 19,95%  | Hora/offset ou  ambiguidade |
| RDT-10  | Órfãos permanecem em STG  | 20.577 itens  | 1,48% dos itens  | Itens ausentes da  fato |
| RDT-11  | Grãos pedido e item separados  | 410.000 pedidos; 1.371.876 itens  | 100% / 98,52%  | ID natural da linha de item |
| RDT-12  | Recuperar SK por CPF único  | 4.290 pedidos  | 1,05% dos  pedidos | IDs secundários não persistidos |
| RDT-13  | LEFT JOIN e protocolo único SAC | 95.000 SAC; 160.000 avaliações; 1.140 duplicações | 100% ligados;  1,19% SAC | Linhas SAC não  repetidas |
| RDT-14  | ABT histórica em 15 features  | 108.064 elegíveis; 7.610 excluídos  | 93,42% / 6,58% clientes | Detalhe transacional e histórico de  versões |
| RDT-15  | 2 features futuras \+ z-score global | 108.064 valores monetários  normalizados | 100% da ABT  | Validade temporal  das previsões |

Os valores das RDT-01 a RDT-13 foram preservados da consolidação do grupo; os números da Semana 4 vêm da execução das ABTs e dos resultados do Orange. Na RDT-15, 100% indica o z-score global, não que todas as recências/frequências tenham mudado.

**Reflexão final**  
**1\. Qual decisão mudaria mais o resultado de um modelo? Vocês testaram ou é hipótese?** O experimento da RDT-15 foi diretamente medido: ao expor recência e frequência a compras posteriores ao corte e normalizar globalmente, a AUC da Tree aumentou de 0,496 para 0,965, e sua acurácia balanceada passou de 51,19% a 94,73%. É um aumento real na avaliação, mas inválido como previsão prospectiva. Outra decisão crítica é a unificação de clientes (RDT-02/03/12): 4.290 pedidos estavam associados indevidamente ao primeiro cliente da dimensão; a correção foi auditada, mas seu efeito isolado na AUC não foi medido.

**2\. Qual decisão por conveniência técnica afetaria pessoas reais?** O fallback arbitrário para um cliente real poderia distorcer frequência e histórico de consumo, atribuindo compras de terceiros a uma pessoa identificável. O uso de dados futuros no modelo também poderia sugerir capacidade de previsão inexistente e motivar ações comerciais indevidas. Reclassificação de cancelamentos, sinais de frete e médias com ausência de informação exigem explicação, porque uma simplificação técnica pode ser interpretada como fato sobre o cliente.

**3\. Qual informação fez falta?** Um identificador universal e uma trilha integral dos IDs descartados na deduplicação; histórico de mudanças de status e categorias para reconstruir a visão exatamente como era em 30/06/2025; canais realmente observados por pedido; timestamps com fuso explícito; e motivos reais de frete negativo e sentinelas. Essas limitações foram documentadas para não confundir a integridade do banco com validade temporal ou semântica das análises.

**Nota metodológica:** a comparação isolada da normalização apontou efeito não perceptível na AUC (três casas decimais). Isso não torna adequado usar estatísticas do conjunto de testes ao preparar um classificador. A escolha segura é ajustar quaisquer parâmetros de pré-processamento somente no treinamento e aplicar a transformação ajustada ao teste.